"""store — cell-per-household storage with a tamper-evident ledger (N2/N4/N11).

Layout:
    <db_root>/cells/<sha16(hid)>/care.db     one SQLite file per household ("cell")
    <db_root>/keyring/<sha16(hid)>.key       the cell's AES-256-GCM data key
    <db_root>/careloop_global.db              households, links, tombstones (no PHI)

A bug or a stolen query can only ever reach one cell. Erasure = destroy the cell
file *and* its key, then write an erasure receipt; ciphertext becomes undecryptable.
Local file deletion is not forensic-grade; in production the key lives in AWS KMS
and DeleteKeyMaterial is the shredding path (ADR-002).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    AESGCM = None
    HAVE_CRYPTO = False

CELL_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS people(
  name TEXT PRIMARY KEY, aliases TEXT NOT NULL DEFAULT '[]', role TEXT NOT NULL DEFAULT 'elder');
CREATE TABLE IF NOT EXISTS items(
  ref TEXT PRIMARY KEY, person TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  sched_text TEXT, sched_json TEXT,
  quote_enc TEXT NOT NULL, qstart INTEGER, qend INTEGER,
  transcript_hash TEXT, dedup_key TEXT, source TEXT, confidence REAL,
  needs_confirm INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT, replaced_by TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, done_at REAL, updated_at REAL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dedup ON items(dedup_key, status) WHERE status IN ('pending','done');
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, type TEXT NOT NULL,
  payload TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS visits(id TEXT PRIMARY KEY, person TEXT, transcript_hash TEXT,
  quote TEXT, created_at REAL NOT NULL, engine TEXT, model TEXT, cost REAL);
CREATE TABLE IF NOT EXISTS idem(key TEXT PRIMARY KEY, ref TEXT, response TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ctx(hid TEXT PRIMARY KEY, person TEXT, refs TEXT, updated_at REAL NOT NULL);
"""

GLOBAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS households(hid TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, pin_hash TEXT NOT NULL,
  tz TEXT DEFAULT 'Asia/Kolkata', meal_anchors TEXT DEFAULT '{"BREAKFAST":"08:00","LUNCH":"13:00","DINNER":"20:00"}',
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS links(link_id TEXT PRIMARY KEY, hid TEXT NOT NULL, sub TEXT NOT NULL,
  display_name TEXT, role TEXT NOT NULL DEFAULT 'viewer', status TEXT NOT NULL DEFAULT 'linked',
  created_at REAL NOT NULL, unlinked_at REAL);
CREATE TABLE IF NOT EXISTS tombstones(receipt TEXT PRIMARY KEY, hid_hash TEXT NOT NULL, erased_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auth_codes(code_hash TEXT PRIMARY KEY, hid TEXT, sub TEXT, link_id TEXT,
  challenge TEXT, method TEXT, client_id TEXT, redirect_uri TEXT, scope TEXT, created_at REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens(token_hash TEXT PRIMARY KEY, kind TEXT, hid TEXT, sub TEXT, link_id TEXT,
  scope TEXT, client_id TEXT, audience TEXT, expires_at REAL, revoked INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS refresh_map(ref_hash TEXT PRIMARY KEY, token_hash TEXT, hid TEXT, sub TEXT,
  link_id TEXT, revoked INTEGER DEFAULT 0, created_at REAL);
"""


def sha256_hex(*chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        if isinstance(c, str):
            c = c.encode("utf-8")
        h.update(c or b"")
    return h.hexdigest()


def _cell_dir(root: str, hid: str) -> str:
    return os.path.join(root, "cells", hashlib.sha1(hid.encode()).hexdigest()[:16])


class StoreLocked(RuntimeError):
    """Raised when a cell is written to while the `db_locked` chaos flag is set."""


def _dec_blob(blob, aes):
    """One decryption path for cells and for the offline ledger verifier."""
    if blob is None or aes is None or not blob.startswith("enc:"):
        return blob
    raw = base64.b64decode(blob[4:])
    return aes.decrypt(raw[:12], raw[12:], b"careloop-field").decode()


def _cell_aes(key_path: str):
    """The cell's data key, if it still exists. A shredded cell has no key, so its ledger
    is unreadable — which is the whole point of crypto-shredding."""
    if not HAVE_CRYPTO or not os.path.exists(key_path):
        return None
    try:
        with open(key_path, "rb") as f:
            return AESGCM(f.read())
    except OSError:
        return None


def _writes_locked() -> bool:
    """The chaos switch is read live, so a running demo can be broken and healed."""
    return _settings is not None and "db_locked" in _settings.chaos


class Cell:
    """One household's database. Never opens another cell."""

    def __init__(self, hid: str, root: str, encrypt: bool = True):
        self.hid = hid
        self.dir = _cell_dir(root, hid)
        os.makedirs(self.dir, exist_ok=True)
        self.db_path = os.path.join(self.dir, "care.db")
        self.key_path = os.path.join(root, "keyring", os.path.basename(self.dir) + ".key")
        os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
        self.encrypt = encrypt and HAVE_CRYPTO
        if self.encrypt:
            if not os.path.exists(self.key_path):
                with open(self.key_path, "wb") as f:
                    f.write(AESGCM.generate_key(bit_length=256))
                os.chmod(self.key_path, 0o600)
            with open(self.key_path, "rb") as f:
                self._key = f.read()
            self._aes = AESGCM(self._key)
        else:
            self._key, self._aes = None, None
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(CELL_SCHEMA)
        self.conn.commit()

    def _writable(self):
        """Fail at the entry of every mutating call: nothing is half-written."""
        if _writes_locked():
            raise StoreLocked("household cell is read-only (chaos: db_locked)")

    # ------------------------------------------------------------ crypto
    def enc(self, text: str) -> str:
        if not self.encrypt or text is None:
            return text
        nonce = secrets.token_bytes(12)
        ct = self._aes.encrypt(nonce, text.encode(), b"careloop-field")
        return "enc:" + base64.b64encode(nonce + ct).decode()

    def dec(self, blob: str) -> str:
        return _dec_blob(blob, self._aes)

    # ------------------------------------------------------------ ledger
    def _ledger_head(self):
        r = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return (r["seq"], r["hash"]) if r else (0, "GENESIS")

    def _event_hash(self, seq, ts, etype, payload, prev_hash):
        return sha256_hex(str(seq), f"{ts:.6f}", etype, payload or "", prev_hash)

    def append_event(self, etype: str, payload: dict) -> dict:
        seq, prev = self._ledger_head()
        ts = time.time()
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        h = self._event_hash(seq + 1, ts, etype, body, prev)
        # The chain hashes the plaintext because that is the content we must be able to prove;
        # the column stores ciphertext because a care event quotes a clinician.
        self.conn.execute("INSERT INTO events(seq, ts, type, payload, prev_hash, hash) VALUES(?,?,?,?,?,?)",
                          (seq + 1, ts, etype, self.enc(body), prev, h))
        self.conn.commit()
        return {"seq": seq + 1, "ts": ts, "type": etype, "hash": h}

    def event_body(self, r) -> str:
        """The plaintext payload of a stored event, or None when it cannot be read."""
        try:
            return self.dec(r["payload"])
        except Exception:
            return None

    def verify_ledger(self) -> dict:
        rows = self.conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        prev = "GENESIS"
        last_seq = 0
        for r in rows:
            if r["seq"] != last_seq + 1:
                return {"ok": False, "broken_at": r["seq"], "reason": "sequence gap"}
            if r["prev_hash"] != prev:
                return {"ok": False, "broken_at": r["seq"], "reason": "prev-hash mismatch"}
            body = self.event_body(r)
            if body is None:
                return {"ok": False, "broken_at": r["seq"],
                        "reason": "payload unreadable (tampering or a destroyed key)"}
            expect = self._event_hash(r["seq"], r["ts"], r["type"], body, prev)
            if expect != r["hash"]:
                return {"ok": False, "broken_at": r["seq"], "reason": "content hash mismatch (tampering?)"}
            prev = r["hash"]
            last_seq = r["seq"]
        return {"ok": True, "events": len(rows), "head": prev}

    def events_as_of(self, until_ts: float):
        return self.conn.execute("SELECT * FROM events WHERE ts <= ? ORDER BY seq", (until_ts,)).fetchall()

    def rebuild_state(self, until_ts=None) -> dict:
        """N5 time-travel: replay the ledger into a plan snapshot."""
        state = {}
        rows = self.conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        for r in rows:
            if until_ts is not None and r["ts"] > until_ts:
                break
            body = self.event_body(r)
            if body is None:
                continue
            p = json.loads(body or "{}")
            t = r["type"]
            if t == "item_added":
                state[p["ref"]] = dict(p, status="pending", version=1)
            elif t == "item_status":
                if p["ref"] in state:
                    state[p["ref"]]["status"] = p["status"]
                    state[p["ref"]]["version"] = state[p["ref"]].get("version", 1) + 1
            elif t == "item_confirmed":
                if p["ref"] in state:
                    state[p["ref"]]["needs_confirm"] = 0
            elif t == "item_deleted":
                state.pop(p["ref"], None)
        return state

    # ------------------------------------------------------------ people
    def add_person(self, name: str, role="elder", aliases=None):
        self._writable()
        name = name.strip()[:40]
        cur = self.conn.execute("SELECT name, aliases FROM people WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if cur:
            merged = sorted(set(json.loads(cur["aliases"]) + [a.lower() for a in (aliases or [])]))
            self.conn.execute("UPDATE people SET aliases=? WHERE name=?", (json.dumps(merged), cur["name"]))
        else:
            self.conn.execute("INSERT INTO people(name, aliases, role) VALUES(?,?,?)",
                              (name, json.dumps([a.lower() for a in (aliases or [])]), role))
        self.conn.commit()
        return name

    def people(self):
        """Aliases are stored as JSON text and handed out as a real list: the companion renders
        `p.aliases.join(', ')` straight off this shape, and a string that merely has a `.length`
        passed its guard then threw `aliases.join is not a function` in the browser."""
        out = []
        for r in self.conn.execute("SELECT * FROM people"):
            p = dict(r)
            try:
                p["aliases"] = json.loads(p["aliases"])
            except (TypeError, ValueError):
                p["aliases"] = []
            out.append(p)
        return out

    def resolve_person(self, query: str):
        """Map 'dad' / 'papa' to a canonical name (case-insensitive)."""
        q = (query or "").strip().lower()
        if not q:
            return None
        for p in self.people():
            if p["name"].lower() == q or q in p["aliases"]:
                return p["name"]
        return None

    def person_role(self, name):
        r = self.conn.execute("SELECT role FROM people WHERE name=? COLLATE NOCASE", (name or "",)).fetchone()
        return r["role"] if r else "viewer"

    # ------------------------------------------------------------ household settings
    def meta_get(self, key: str):
        r = self.conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return r["v"] if r else None

    def meta_set(self, key: str, value: str):
        self._writable()
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (key, value))
        self.conn.commit()

    def voice_scope(self):
        """The household's own statement of who may hear whose plan on the voice surface, or []
        when it has set none. Entries are keyed by a claimed speaker; a speaker with no entry is
        bounded by nothing. Names are matched against the household's people by policy_guard."""
        raw = self.meta_get("voice_scope")
        if not raw:
            return []
        try:
            entries = json.loads(raw)
        except ValueError:
            return []
        return entries if isinstance(entries, list) else []

    def set_voice_scope(self, entries):
        """Stored sorted by speaker so the same boundary always writes the same bytes."""
        self._writable()
        ordered = sorted(list(entries or []), key=lambda e: str(e.get("speaker", "")).lower())
        self.meta_set("voice_scope", json.dumps(ordered, ensure_ascii=False, sort_keys=True))
        return ordered

    # ------------------------------------------------------------ items
    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()

    def add_item(self, *, person, kind, sched_text, sched_json, quote, qstart, qend,
                 transcript_hash, source, confidence, needs_confirm, dedup_text=None, visit_id=None,
                 idem_key=None):
        self._writable()
        norm = self._norm(dedup_text or quote)
        dedup_key = sha256_hex(self.hid, person, kind, norm)
        existing = self.conn.execute(
            "SELECT * FROM items WHERE dedup_key=? AND status IN ('pending','done')", (dedup_key,)).fetchone()
        if existing:  # FR-E6: dedupe across statuses, never resurrect
            return self._item_out(existing), "deduped"
        ref = "i-" + secrets.token_hex(4)
        ts = time.time()
        enc_quote = self.enc(quote)
        if idem_key:
            done = self.conn.execute("SELECT response FROM idem WHERE key=?", (sha256_hex(self.hid, idem_key),)).fetchone()
            if done:
                return json.loads(done["response"]), "replayed"
        self.conn.execute(
            """INSERT INTO items(ref, person, kind, status, sched_text, sched_json, quote_enc,
               qstart, qend, transcript_hash, dedup_key, source, confidence, needs_confirm,
               version, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (ref, person, kind, "pending", sched_text, json.dumps(sched_json), enc_quote,
             qstart, qend, transcript_hash, dedup_key, source, confidence, 1 if needs_confirm else 0, ts, ts))
        self.append_event("item_added", {"ref": ref, "person": person, "kind": kind,
                                         "quote": quote[:400], "qstart": qstart, "qend": qend,
                                         "transcript_hash": transcript_hash, "visit_id": visit_id,
                                         "source": source, "confidence": confidence})
        if idem_key:
            self.conn.execute("INSERT OR REPLACE INTO idem(key, ref, response, created_at) VALUES(?,?,?,?)",
                              (sha256_hex(self.hid, idem_key), ref, json.dumps({"ref": ref}), ts))
        self.conn.commit()
        return self.get_item(ref), "added"

    def get_item(self, ref):
        r = self.conn.execute("SELECT * FROM items WHERE ref=?", (ref,)).fetchone()
        return self._item_out(r) if r else None

    def _item_out(self, r: dict | sqlite3.Row) -> dict:
        d = dict(r)
        d["quote"] = self.dec(d.pop("quote_enc"))
        d["text"] = d["quote"]  # extractive-by-construction: the shown text IS the clinician sentence
        d["sched_json"] = json.loads(d.get("sched_json") or "{}")
        d["needs_confirm"] = bool(d["needs_confirm"])
        return d

    def list_items(self, person=None, kinds=None, statuses=("pending", "done", "superseded")) -> list:
        q = "SELECT * FROM items WHERE 1=1"
        args = []
        if statuses:
            q += " AND status IN (%s)" % ",".join("?" * len(statuses))
            args += list(statuses)
        if person:
            q += " AND person=? COLLATE NOCASE"
            args.append(person)
        if kinds:
            q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        q += " ORDER BY created_at"
        return [self._item_out(r) for r in self.conn.execute(q, args)]

    def mark_done(self, ref, who=None, idem_key=None, version=None) -> dict:
        self._writable()
        item = self.get_item(ref)
        if not item:
            return {"error": "not-found"}
        if idem_key:
            k = sha256_hex(self.hid, "done", idem_key)
            done = self.conn.execute("SELECT response FROM idem WHERE key=?", (k,)).fetchone()
            if done:
                return dict(json.loads(done["response"]), replayed=True)
        if version is not None and version != item["version"]:
            return {"error": "version-conflict", "current_version": item["version"]}
        ts = time.time()
        self.conn.execute("UPDATE items SET status='done', done_at=?, version=version+1, updated_at=? WHERE ref=?",
                          (ts, ts, ref))
        self.append_event("item_status", {"ref": ref, "status": "done", "who": who})
        resp = {"ref": ref, "status": "done"}
        if idem_key:
            self.conn.execute("INSERT OR REPLACE INTO idem(key, ref, response, created_at) VALUES(?,?,?,?)",
                              (sha256_hex(self.hid, "done", idem_key), ref, json.dumps(resp), ts))
        self.conn.commit()
        return resp

    def set_version(self, ref):
        it = self.get_item(ref)
        return it["version"] if it else None

    def confirm_item(self, ref) -> dict:
        self._writable()
        item = self.get_item(ref)
        if not item:
            return {"error": "not-found"}
        ts = time.time()
        self.conn.execute("UPDATE items SET needs_confirm=0, version=version+1, updated_at=? WHERE ref=?", (ts, ref))
        self.append_event("item_confirmed", {"ref": ref})
        self.conn.commit()
        return {"ref": ref, "needs_confirm": False}

    def supersede(self, old_ref, new_ref):
        self._writable()
        ts = time.time()
        self.conn.execute("UPDATE items SET status='superseded', superseded_by=?, version=version+1, updated_at=? WHERE ref=?",
                          (new_ref, ts, old_ref))
        self.conn.execute("UPDATE items SET replaced_by=? WHERE ref=?", (new_ref, old_ref))
        self.conn.execute("UPDATE items SET version=version+1 WHERE ref=?", (new_ref,))
        self.append_event("item_status", {"ref": old_ref, "status": "superseded", "by": new_ref})
        self.conn.commit()

    def delete_item(self, ref, confirm=False) -> dict:
        self._writable()
        item = self.get_item(ref)
        if not item:
            return {"error": "not-found"}
        if not confirm:  # Functional §8: read-back then explicit confirmation
            return {"needs_confirmation": True,
                    "summary": {"ref": ref, "person": item["person"], "kind": item["kind"],
                                "quote": item["quote"]}}
        ts = time.time()
        self.conn.execute("DELETE FROM items WHERE ref=?", (ref,))
        self.append_event("item_deleted", {"ref": ref, "kind": item["kind"], "person": item["person"]})
        self.conn.commit()
        return {"deleted": True, "ref": ref}

    def flag(self, ref, needs_confirm=True):
        self._writable()
        self.conn.execute("UPDATE items SET needs_confirm=? WHERE ref=?", (1 if needs_confirm else 0, ref))
        self.conn.commit()

    # ------------------------------------------------------------ visits / diff
    def record_visit(self, person, transcript, engine, model, cost):
        self._writable()
        vid = "v-" + secrets.token_hex(4)
        th = sha256_hex(transcript)
        ts = time.time()
        self.conn.execute("INSERT INTO visits(id, person, transcript_hash, quote, created_at, engine, model, cost)"
                          " VALUES(?,?,?,?,?,?,?,?)", (vid, person, th, self.enc(th[:12]), ts, engine, model, cost))
        self.append_event("visit_recorded", {"visit_id": vid, "person": person, "transcript_hash": th,
                                             "engine": engine, "model": model})
        self.conn.commit()
        return vid, th

    def visits(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM visits ORDER BY created_at")]

    def what_changed(self, since_ts) -> dict:
        """Diff items created before/after a timestamp boundary."""
        rows = self.list_items(statuses=("pending", "done", "superseded"))
        new = [r for r in rows if r["created_at"] >= since_ts and r["status"] != "superseded"]
        changed = [r for r in rows if r["updated_at"] >= since_ts > r["created_at"] and r["status"] == "superseded"]
        prior = [r for r in rows if r["created_at"] < since_ts]
        prior_keys = {r["dedup_key"] for r in prior}
        active_new = {r["dedup_key"] for r in new if r["status"] == "pending"}
        stopped = [r for r in prior if r["dedup_key"] not in active_new and r["status"] in ("superseded",)]
        return {"new": new, "stopped": stopped, "changed": changed}

    # ------------------------------------------------------------ adherence
    def adherence(self, day_iso=None):
        q = "SELECT i.person, i.kind, i.status, i.done_at, i.created_at FROM items i"
        rows = self.conn.execute(q).fetchall()
        done = [r for r in rows if r["status"] == "done" and
                (day_iso is None or time.strftime("%Y-%m-%d", time.localtime(r["done_at"])) == day_iso)]
        pending = [r for r in rows if r["status"] == "pending"]
        return {"done_today": len(done), "pending": len(pending), "history": len(done)}

    # ------------------------------------------------------------ context (N23)
    def set_context(self, person, refs, ttl):
        if _writes_locked():   # conversational context is disposable; answering must not break
            return
        self.conn.execute("INSERT OR REPLACE INTO ctx(hid, person, refs, updated_at) VALUES(?,?,?,?)",
                          (self.hid, person, json.dumps(refs), time.time()))
        self.conn.commit()

    def get_context(self, ttl):
        r = self.conn.execute("SELECT * FROM ctx WHERE hid=?", (self.hid,)).fetchone()
        if not r:
            return None
        if time.time() - r["updated_at"] > ttl:
            self.conn.execute("DELETE FROM ctx WHERE hid=?", (self.hid,))
            self.conn.commit()
            return {"expired": True}
        return {"person": r["person"], "refs": json.loads(r["refs"])}

    def clear_context(self):
        if _writes_locked():
            return
        self.conn.execute("DELETE FROM ctx WHERE hid=?", (self.hid,))
        self.conn.commit()

    # ------------------------------------------------------------ shredding
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def shred(self) -> dict:
        """Crypto-shredding: destroy the key first, then the cell, then write a receipt.

        The receipt is only issued when the bytes really went. A WAL sidecar held open by
        another connection can still carry data, so we report `erased: false` instead of
        claiming an erasure that did not happen.
        """
        self._writable()   # a shredding that cannot be receipted must not start
        self.close()
        hid_hash = sha256_hex(self.hid)[:32]
        reason = None
        for p in (self.key_path, self.db_path, self.db_path + "-wal", self.db_path + "-shm"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError as exc:
                reason = f"{os.path.basename(p)}: {exc.strerror or 'in use'}"
        try:
            os.rmdir(self.dir)
        except OSError:
            pass
        left = [p for p in (self.db_path, self.db_path + "-wal", self.key_path) if os.path.exists(p)]
        if left:
            return {"erased": False,
                    "left_behind": [os.path.basename(p) for p in left],
                    "reason": reason or "cell still open elsewhere",
                    "note": "No erasure receipt: the data (or its key) is still on disk."}
        receipt = "er-" + secrets.token_hex(6)
        add_tombstone(receipt, hid_hash)
        return {"erased": True, "receipt": receipt,
                "note": "Local deletion is not forensic-grade; production path = KMS DeleteKeyMaterial."}


# ================================================================ global db
class Global:
    def __init__(self, root: str):
        os.makedirs(root, exist_ok=True)
        self.conn = sqlite3.connect(os.path.join(root, "careloop_global.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(GLOBAL_SCHEMA)
        self.conn.commit()

    def create_household(self, code: str, pin: str, tz="Asia/Kolkata") -> str:
        code = re.sub(r"[^A-Za-z0-9]", "", code).upper()[:12]
        if not code:
            raise ValueError("household code must be alphanumeric")
        hid = sha256_hex("household", code)[:16]
        exists = self.conn.execute("SELECT hid FROM households WHERE code=?", (code,)).fetchone()
        if exists:
            return exists["hid"]
        self.conn.execute("INSERT INTO households(hid, code, pin_hash, tz, created_at) VALUES(?,?,?,?,?)",
                          (hid, code, sha256_hex("pin", pin), tz, time.time()))
        self.conn.commit()
        return hid

    def household_by_code(self, code):
        return self.conn.execute("SELECT * FROM households WHERE code=?",
                                 (code.strip().upper()[:12],)).fetchone()

    def check_pin(self, hid, pin) -> bool:
        r = self.conn.execute("SELECT pin_hash FROM households WHERE hid=?", (hid,)).fetchone()
        return bool(r) and hmac.compare_digest(r["pin_hash"], sha256_hex("pin", pin))

    def verify_quote_hash(self, hid, quote_hash) -> bool:
        """Companion shows a quote hash; user can confirm the household held that quote."""
        cell = open_cell(hid, encrypt=False)
        r = cell.conn.execute("SELECT transcript_hash FROM visits").fetchall()
        cell.close()
        return any(row["transcript_hash"] == quote_hash for row in r)

    def add_link(self, hid, display_name, role, sub=None) -> str:
        link_id = "l-" + secrets.token_hex(4)
        sub = sub or display_name.strip().lower().replace(" ", ".")[:32]
        self.conn.execute("INSERT INTO links(link_id, hid, sub, display_name, role, created_at)"
                          " VALUES(?,?,?,?,?,?)", (link_id, hid, sub, display_name.strip()[:40], role, time.time()))
        self.conn.commit()
        return link_id

    def links(self, hid):
        return [dict(r) for r in self.conn.execute(
            "SELECT link_id, sub, display_name, role, status FROM links WHERE hid=? AND status='linked'", (hid,))]

    def link(self, link_id):
        return self.conn.execute("SELECT * FROM links WHERE link_id=?", (link_id,)).fetchone()

    def unlink(self, link_id):
        self.conn.execute("UPDATE links SET status='unlinked', unlinked_at=? WHERE link_id=?",
                          (time.time(), link_id))
        # Functional §6: data of a previous link must not be reusable — revoke its tokens
        self.conn.execute("UPDATE tokens SET revoked=1 WHERE link_id=?", (link_id,))
        self.conn.execute("UPDATE refresh_map SET revoked=1 WHERE link_id=?", (link_id,))
        self.conn.commit()


_TOMB = None


def add_tombstone(receipt, hid_hash):
    g = global_db()
    g.conn.execute("INSERT INTO tombstones(receipt, hid_hash, erased_at) VALUES(?,?,?)",
                   (receipt, hid_hash, time.time()))
    g.conn.commit()


def tombstones():
    g = global_db()
    return [dict(r) for r in g.conn.execute("SELECT * FROM tombstones ORDER BY erased_at DESC")]


# ================================================================ registry
_settings = None
_global = None
_cells = {}


def init(settings):
    global _settings, _global
    _settings = settings
    _global = Global(settings.db_root)
    return _global


def global_db() -> Global:
    if _global is None:
        raise RuntimeError("store.init() not called")
    return _global


def open_cell(hid: str, encrypt=None) -> Cell:
    if _settings is None:
        raise RuntimeError("store.init() not called")
    enc = _settings.encryption if encrypt is None else encrypt
    return Cell(hid, _settings.db_root, encrypt=enc)


def verify_all_cells() -> dict:
    out = {}
    root = os.path.join(_settings.db_root, "cells")
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            db = os.path.join(root, name, "care.db")
            if os.path.exists(db):
                aes = _cell_aes(os.path.join(_settings.db_root, "keyring", name + ".key"))
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
                conn.close()
                prev = "GENESIS"
                ok, at, why = True, None, None
                for r in rows:
                    try:
                        body = _dec_blob(r["payload"], aes)
                    except Exception:
                        body = None
                    expect = sha256_hex(str(r["seq"]), f"{r['ts']:.6f}", r["type"], body or "", prev)
                    if body is None:
                        ok, at, why = False, r["seq"], "payload unreadable"
                        break
                    if expect != r["hash"] or r["prev_hash"] != prev:
                        ok, at, why = False, r["seq"], "hash mismatch"
                        break
                    prev = r["hash"]
                out[name] = {"ok": ok, "events": len(rows), "broken_at": at,
                             **({"reason": why} if why else {})}
    return out
