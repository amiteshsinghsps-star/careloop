"""Offline demo bundle — a game-day safety net that needs no network, no AWS and no typing.

    python scripts/offline_bundle.py make      [--out artifacts/bundle]
    python scripts/offline_bundle.py verify    [--out artifacts/bundle]
    python scripts/offline_bundle.py restore   [--out artifacts/bundle] [--db-root data/offline]

`restore` seeds households and cells straight from the bundled transcripts with the
deterministic rules extractor, so the whole loop (plan, today's doses masked for voice,
companion full doses, ledger) is alive before anyone reaches a keyboard. Every synthetic
adult visit; no real patient data, no pediatric scenarios.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEMO_VISITS = [
    {"code": "CASA01", "pin": "481542", "person": "Papa",
     "transcript": ("Doctor: Your haemoglobin is low. Take 200 mg iron after dinner every day for two months. "
                    "Doctor: Avoid tea with meals because it blocks the iron. "
                    "Nurse: Check the blood pressure every morning and write it down. "
                    "Doctor: Come back after six weeks.")},
    {"code": "MART02", "pin": "730118", "person": "Ruth",
     "transcript": ("Doctor: Take metformin 500 mg twice a day with breakfast and dinner. "
                    "Doctor: Take atorvastatin 10 mg at bedtime. "
                    "Doctor: Call the clinic if you feel dizzy or faint. "
                    "Nurse: Walk for twenty minutes after lunch most days.")},
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def settings_for(db_root):
    os.environ["CARELOOP_DB_ROOT"] = db_root
    os.environ["CARELOOP_AUTH"] = "off"
    os.environ["CARELOOP_DEMO"] = "0"
    os.environ.pop("CARELOOP_CHAOS", None)
    os.environ["CARELOOP_ENCRYPT_FIELDS"] = "0"
    for name in [m for m in list(sys.modules) if m.startswith("careloop")]:
        del sys.modules[name]
    from careloop.config import Settings
    from careloop import store
    s = Settings()
    store.init(s)
    return s, store


def cmd_make(out):
    os.makedirs(os.path.join(out, "transcripts"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    manifest = {"kind": "careloop-offline-demo-bundle", "version": 1,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "households": [{"code": v["code"], "person": v["person"]} for v in DEMO_VISITS],
                "data": "synthetic adult visits only — no real patient information",
                "files": []}
    for v in DEMO_VISITS:
        p = os.path.join(out, "transcripts", f"{v['code']}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(v["transcript"])
    fixtures = os.environ.get("CARELOOP_RECORD_DIR", "")
    copied = []
    if fixtures and os.path.isdir(fixtures):
        dst = os.path.join(out, "fixtures")
        os.makedirs(dst, exist_ok=True)
        for name in sorted(os.listdir(fixtures)):
            if name.endswith(".json"):
                shutil.copy(os.path.join(fixtures, name), dst)
                copied.append(name)
    for dirpath, _, files in os.walk(out):
        for name in sorted(files):
            if name == "bundle.json":
                continue
            full = os.path.join(dirpath, name)
            manifest["files"].append({"path": os.path.relpath(full, out).replace("\\", "/"),
                                      "sha256": sha256_file(full), "bytes": os.path.getsize(full)})
    manifest["fixtures"] = copied
    with open(os.path.join(out, "bundle.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"{out}: {len(manifest['files'])} files, {len(DEMO_VISITS)} households, "
          f"{len(copied)} recorded fixtures")
    return 0


def read_bundle(out):
    with open(os.path.join(out, "bundle.json"), encoding="utf-8") as f:
        return json.load(f)


def bundled_visits(out):
    """Transcripts come from the bundle if it is there, else from the built-in set."""
    visits = []
    for v in DEMO_VISITS:
        p = os.path.join(out, "transcripts", f"{v['code']}.txt")
        text = v["transcript"]
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                text = f.read()
        visits.append(dict(v, transcript=text))
    return visits


def cmd_restore(out, db_root, verbose=True):
    from careloop import extract
    _s, store = settings_for(db_root)
    g = store.global_db()
    totals = []
    for v in bundled_visits(out):
        hid = g.create_household(v["code"], v["pin"])
        if not g.links(hid):
            g.add_link(hid, "demo-caregiver", "caregiver")
        cell = store.open_cell(hid, encrypt=False)
        cell.add_person(v["person"])
        existing = cell.list_items(person=v["person"], statuses=("pending",))
        res = extract.process_visit(v["transcript"], _s)
        if existing:   # already seeded — restore is idempotent, it does not stack visits
            totals.append((v["code"], len(existing), "already-seeded"))
            if verbose:
                print(f"  {v['code']}: {len(existing)} items already present, left as they are")
            cell.close()
            continue
        cell.record_visit(v["person"], v["transcript"], res["engine"], res["model"], res["cost"])
        made = []
        for it in res["items"]:
            item, status = cell.add_item(person=v["person"], kind=it["kind"], sched_text=it["schedule"],
                                         sched_json=it["sched_json"], quote=it["quote"][:600],
                                         qstart=it["qstart"], qend=it["qend"],
                                         transcript_hash=store.sha256_hex(v["transcript"]),
                                         source=it["engine"], confidence=it["confidence"],
                                         needs_confirm=it["needs_confirm"])
            if status == "added":
                made.append(item)
        cell.close()
        totals.append((v["code"], len(made), res["engine"]))
        if verbose:
            print(f"  {v['code']}: {len(made)} items via {res['engine']}")
    return totals


def cmd_verify(out):
    from careloop import policy_guard as pg
    manifest = read_bundle(out)
    bad = []
    for entry in manifest["files"]:
        p = os.path.join(out, entry["path"])
        if not os.path.exists(p) or sha256_file(p) != entry["sha256"]:
            bad.append(entry["path"])
    if bad:
        print("bundle files missing or changed: " + ", ".join(bad))
        return 1
    tmp = tempfile.mkdtemp(prefix="careloop-bundle-")
    try:
        _s, store = settings_for(tmp)
        from careloop import extract
        total = 0
        for v in bundled_visits(out):
            res = extract.process_visit(v["transcript"], _s)
            sents = extract.sentences(v["transcript"])
            for it in res["items"]:
                if not extract.quote_guard(it["text"], sents, v["transcript"]):
                    print(f"  UNGROUNDED in {v['code']}: {it['text']!r}")
                    return 1
                masked = pg.mask_doses(it["text"])
                if pg.contains_dose(masked):
                    print(f"  DOSE SURVIVES MASKING in {v['code']}: {masked!r}")
                    return 1
            total += len(res["items"])
        if not total:
            print("the bundled transcripts extract no items at all")
            return 1
        print(f"bundle ok: {len(manifest['files'])} files, {total} grounded items, "
              f"every masked string dose-free")
        return 0
    finally:
        store._global = None
        store._settings = None
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["make", "verify", "restore"])
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "bundle"))
    ap.add_argument("--db-root", default=os.path.join(ROOT, "data", "offline"))
    args = ap.parse_args()
    if args.command == "make":
        return cmd_make(args.out)
    if args.command == "verify":
        return cmd_verify(args.out)
    totals = cmd_restore(args.out, args.db_root)
    print(f"seeded {len(totals)} households into {args.db_root}")
    print(f"start it with:  CARELOOP_DB_ROOT={args.db_root} python scripts/serve.py --auth off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
