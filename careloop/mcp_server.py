"""mcp_server — the whole HTTP app: MCP Streamable HTTP endpoint plus the companion
capture API, OAuth endpoints, simulated Alexa+ page, judge panel and metrics.

Hardening implemented from the MCP 2025-11-25 transport spec and the Alexa+ docs
(Part D.2): Origin validation -> 403, MCP-Protocol-Version -> 400, notifications ->
202, Accept rules, MCP-Session-Id semantics, 401 without WWW-Authenticate, body cap,
audience-bound tokens, no credentials in query strings, structured logs without PHI.
"""
import hmac
import json
import logging
import os
import re
import secrets
import select
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import oauth, policy_guard as pg, store, timecraft, tools
from .config import settings as S
from .extract import process_visit

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "careloop", "version": "6.0.0"}
CARD_URI = tools.CARD_URI

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Built frontends land here (see scripts/sync_web.mjs + `make dist`).
DIST_DIRS = ("site", "simulator", "companion")
# URL prefix -> (dist dir, entry file). SPA paths fall back to the entry file.
MOUNTS = {"/simulator": ("simulator", "index.html"),
          "/judge": ("simulator", "judge.html"),
          "/companion": ("companion", "index.html")}
SITE_ROUTES = ("/privacy", "/terms", "/docs", "/blog", "/a11y-lab")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
        ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
        ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",
        ".webmanifest": "application/manifest+json"}
HASHED_MARKERS = ("/_astro/", "/assets/", "/media/", ".vite/")
# A policy delivered *with* a resource also governs that resource's own fetches in Chromium.
# That lock-down is correct for anything the browser can render as a document, but on a script
# or stylesheet it stops a built bundle loading its own chunks (measured: the companion's
# login route never appeared). See _security.
SUBRESOURCE_TYPES = ("text/javascript", "application/javascript", "text/css")

LINK_DONE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CareLoop — linking finished</title>
<link rel="stylesheet" href="/static/base.css"></head><body class="center">
<main class="panel" role="main"><h1>Linking finished</h1>
<p>You can close this tab and go back to your assistant. Nothing more is needed here.</p>
<p class="small">If the assistant says it is still not linked, say &ldquo;relink CareLoop&rdquo; and try again.
Not medical advice.</p></main></body></html>"""

log = logging.getLogger("careloop")
logging.basicConfig(level=os.environ.get("CARELOOP_LOG", "INFO"),
                    format="%(message)s")

_sessions = {}            # session id -> {"hid": str|None}
_lock = threading.Lock()
_metrics = {"tools": {}, "errors": {}, "requests": 0, "started": time.time(),
            "escalations": 0, "cost": 0.0}
_streams = {"n": 0}          # open SSE connections; each one holds a thread and a cell
_STREAM_MAX, _STREAM_TTL, _STREAM_PING_S = 8, 900.0, 15.0
_STREAM_TICK_S, _STREAM_POLL_S = 0.25, 1.0   # hang-up check cadence, plan-change cadence


def _rid():
    return secrets.token_hex(4)


class Metric:
    def __init__(self, name):
        self.name, self.t0 = name, time.time()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        ms = (time.time() - self.t0) * 1000
        with _lock:
            _metrics["tools"].setdefault(self.name, []).append(round(ms, 1))
            lst = _metrics["tools"][self.name][-500:]
            _metrics["tools"][self.name] = lst


def percentiles(xs):
    if not xs:
        return {"p50": None, "p95": None, "p99": None, "n": 0}
    s = sorted(xs)
    def p(q):
        return s[min(len(s) - 1, int(q * len(s)))]
    return {"p50": p(0.50), "p95": p(0.95), "p99": p(0.99), "n": len(s)}


# ================================================================ JSON-RPC core
def rpc_result(payload):
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": payload.get("result", {})}


class MCP:
    """Handles one /mcp request. Returns (status, headers, body_bytes)."""

    def __init__(self, handler_ctx):
        self.ctx = handler_ctx

    def handle(self, body: bytes, headers) -> tuple:
        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception:
            return self._err(None, -32700, "Parse error", 400)
        if isinstance(msg, list):
            return self._err(msg[0].get("id") if isinstance(msg[0], dict) else None,
                             -32600, "Batch requests are not supported on this stream", 400)
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._err(None, -32600, "Invalid request", 400)
        method = msg.get("method", "")
        is_notification = "id" not in msg and method.startswith(("notifications/",))
        if method == "initialize":
            return self.initialize(msg, headers)
        # protocol-version header for initialised requests
        pv = headers.get("MCP-Protocol-Version")
        if pv is not None and pv not in PROTOCOLS:
            return self._err(msg.get("id"), -32600, f"Unsupported MCP-Protocol-Version {pv}", 400)
        sid = headers.get("MCP-Session-Id")
        if sid is not None and sid not in _sessions:
            return self._err(msg.get("id"), -32001, "Session not found; re-initialize", 404)
        if is_notification:
            return 202, {"Content-Type": "application/json"}, b""
        tok = self.ctx["token"]
        if method == "ping":
            return self._ok(msg, {})
        if method == "tools/list":
            if not tok:
                return self._err(msg.get("id"), -32001, "Unauthorized", 401)
            return self._ok(msg, {"tools": [
                {k: d[k] for k in ("name", "description", "inputSchema", "outputSchema")
                 if k in d} | ({"annotations": d["annotations"]} if d.get("annotations") else {})
                for d in tools.TOOL_DEFS]})
        if method == "resources/list":
            return self._ok(msg, {"resources": [
                {"uri": CARD_URI, "name": "Care plan card", "mimeType": "text/html;profile=mcp-app",
                 "description": "Large-text care plan card with Done buttons."}]})
        if method == "resources/templates/list":
            return self._ok(msg, {"resourceTemplates": []})
        if method == "resources/read":
            uri = (msg.get("params") or {}).get("uri", "")
            if uri != CARD_URI:
                return self._err(msg.get("id"), -32002, "Unknown resource", 404)
            with open(os.path.join(os.path.dirname(__file__), "web", "card.html"), "rb") as f:
                html = f.read()
            return self._ok(msg, {"contents": [{"uri": uri, "mimeType": "text/html;profile=mcp-app",
                                                "text": html.decode("utf-8")}]})
        if method == "tools/call":
            return self.tools_call(msg, tok)
        return self._err(msg.get("id"), -32601, f"Method not found: {method}", 200)

    def _ok(self, msg, result):
        return 200, {"Content-Type": "application/json"}, \
            json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}).encode()

    def _err(self, mid, code, message, status, extra_headers=None):
        body = json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        return status, headers, body.encode()

    # ------------------------------------------------------------- initialize
    def initialize(self, msg, headers):
        tok = self.ctx["token"]
        if not tok or tok["scope"] not in ("mcp:service", "mcp:tools"):
            return self._err(msg.get("id"), -32001,
                             "A service token from the linked account is required before initializing.", 401)
        params = msg.get("params") or {}
        requested = params.get("protocolVersion", "2025-03-26")
        chosen = requested if requested in PROTOCOLS else PROTOCOLS[0]
        sid = "cl-" + secrets.token_hex(8)
        with _lock:
            _sessions[sid] = {"hid": tok.get("hid")}
        info = (params.get("clientInfo") or {}).get("name", "")
        log.debug(json.dumps({"evt": "initialize", "rid": self.ctx["rid"], "client": info,
                              "protocol": chosen}))
        result = {"protocolVersion": chosen, "serverInfo": SERVER_INFO,
                  "capabilities": {"tools": {"listChanged": True},
                                   "resources": {"subscribe": False, "listChanged": False},
                                   "extensions": {"mcp-apps": "2026-01-26"}}}
        status, h, body = self._ok(msg, result)
        h["MCP-Session-Id"] = sid
        return status, h, body

    # ------------------------------------------------------------- tools/call
    def tools_call(self, msg, tok):
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        req_meta = params.get("_meta") or {}
        defn = next((d for d in tools.TOOL_DEFS if d["name"] == name), None)
        if defn is None:
            return self._err(msg.get("id"), -32602, f"Unknown tool: {name}", 200)
        if not tok:
            return self._err(msg.get("id"), -32001, "Unauthorized", 401)
        bad = tools.validate_args(defn, args)
        if bad:
            return self._tool_error(msg, "Some details didn't fit: " + "; ".join(bad[:2]) + ".",
                                    "Say the request again in your own words.")
        if tok["kind"] == "service":
            if name not in tools.GUEST_SAFE:
                # triggers the linking flow: 401 with WWW-Authenticate per spec
                return self._err(msg.get("id"), -32001,
                                 "This question needs the household account linked.", 401,
                                 extra_headers={"WWW-Authenticate":
                                     f'Bearer scope="mcp:tools", resource_metadata='
                                     f'"{S.public_url}/.well-known/oauth-protected-resource"'})
            surface, identity = "alexa", {"hid": None, "role": "guest"}
        else:
            identity = oauth.link_identity(tok)
            if identity is None:
                return self._err(msg.get("id"), -32001,
                                 "The linked session is no longer active.",
                                 401, extra_headers={"WWW-Authenticate": "Bearer"})
            decision = tools.authorize(defn, identity, "alexa")
            if not decision:
                return self._tool_error(msg, "Your household role isn't allowed to do that on the voice "
                                             "surface.", "Open the CareLoop companion page to manage the plan.")
            surface = "alexa"
        cell = store.open_cell(identity["hid"]) if identity.get("hid") else None
        ctx = {"ttl": S.context_ttl, "last_refs": [], "last_refs_by_person": {}}
        if cell:
            stored = cell.get_context(S.context_ttl)
            if stored and not stored.get("expired"):
                ctx["last_refs"] = stored.get("refs", [])
                ctx["person"] = stored.get("person")
            elif stored and stored.get("expired"):
                ctx["expiry_note"] = "Earlier answers are no longer on screen."
        # ---- speaker scope: decided before the tool runs, so a refused call reads and writes
        # nothing. A device's claim buys nothing on its own (see policy_guard.resolve_claim).
        verdict, speaker = pg.resolve_claim(req_meta, cell.people() if cell else [])
        boundary = pg.scope_entry(cell.voice_scope(), speaker) if speaker else None
        refused = self._speaker_refusal(verdict, speaker, defn, args, ctx, cell, req_meta) if cell else None
        if refused:
            message, step, event = refused
            try:
                cell.append_event("speaker_refused", event)
            finally:
                cell.close()
            _count_error(f"{name}.speaker-refused")
            return self._tool_error(msg, message, step, {"speaker_refused": event["reason"]})
        t0 = time.time()
        try:
            with Metric(f"tool:{name}"):
                if "latency_800ms" in S.chaos:
                    time.sleep(0.8)   # inside the timer: the p95 SLO flag has to fail honestly
                structured = HANDLERS_RUN(defn, ctx, cell, args)
            if (boundary and boundary.get("may_hear_pending") is False
                    and defn["annotations"].get("readOnlyHint") and pg.reveals_pending(structured)):
                # only a read is refused after it ran: refusing a write would leave the caller
                # told nothing happened when something did
                raise _SpeakerRefused(pg.SPEAKER_PENDING_HELD, pg.SPEAKER_PENDING_HELD_NEXT,
                                      {"speaker": speaker, "tool": name, "reason": "pending-held"})
        except tools.ToolError as te:
            _count_error(name)
            return self._tool_error(msg, te.message, te.next_step)
        except _SpeakerRefused as sr:
            _count_error(f"{name}.speaker-refused")
            cell.append_event("speaker_refused", sr.event)
            return self._tool_error(msg, sr.message, sr.next_step,
                                    {"speaker_refused": sr.event["reason"]})
        except store.StoreLocked:
            _count_error(name + ".store-locked")
            return self._tool_error(msg, "CareLoop is briefly read-only, so nothing was changed.",
                                    "Please ask me again in a minute.")
        except Exception:
            _count_error(name)
            log.exception(json.dumps({"evt": "tool-internal", "rid": self.ctx["rid"], "tool": name}))
            return self._tool_error(msg, "Something went wrong on our side; nothing was changed.",
                                    "Please try again in a moment.")
        finally:
            if cell:
                cell.close()
        structured = pg.guard_payload(structured, surface=surface)
        if ctx.get("expiry_note"):
            structured.setdefault("notes", []).append(ctx["expiry_note"])
        text = spoken_text(structured)
        result = {"content": [{"type": "text", "text": text}],
                  "structuredContent": structured, "isError": False}
        meta = {"ui": {"resourceUri": CARD_URI, "invoking": "Putting the care plan on your screen...",
                       "invoked": "Care plan on screen."}} if defn.get("ui") else {}
        if meta:
            result["_meta"] = meta
        leaks = pg.dose_leak_in(result)
        if leaks:  # kill-criterion guard: never emit, but count and sanitize hard
            _count_error(name + ".dose-leak")
            result = json.loads(pg.mask_deep(json.dumps(result)))
        _log_call(name, int((time.time() - t0) * 1000), self.ctx["rid"])
        return self._ok(msg, result)

    def _tool_error(self, msg, message, next_step, extra=None):
        text = (message + " " + next_step).strip()
        structured = {"error": text}
        structured.update(extra or {})
        return self._ok(msg, {"content": [{"type": "text", "text": text}],
                              "structuredContent": structured, "isError": True})

    def _speaker_refusal(self, verdict, speaker, defn, args, ctx, cell, req_meta):
        """Refused before the tool runs, so a bounded call neither reads nor writes anything.
        Every sentence here is a policy_guard constant; the reason is recorded in this household's
        own ledger. Returns (message, next_step, event) or None when the call may go ahead."""
        claim = str((req_meta or {}).get(pg.SPEAKER_META_KEY) or "")[:40]
        if verdict == "bad-profile":
            return (pg.SPEAKER_BAD_PROFILE, pg.SPEAKER_BAD_PROFILE_NEXT,
                    {"claim": claim, "tool": defn["name"], "reason": "profile-unrecognised"})
        if verdict == "unknown":
            return (pg.SPEAKER_UNKNOWN, pg.SPEAKER_UNKNOWN_NEXT,
                    {"claim": claim, "tool": defn["name"], "reason": "speaker-unknown"})
        if verdict != "ok":
            return None
        everyone = [p["name"] for p in cell.people()]
        concerned = self._concerned_person(cell, defn, args, ctx)
        if not pg.may_hear_people(pg.scope_entry(cell.voice_scope(), speaker), concerned, everyone):
            return (pg.SPEAKER_OUT_OF_SCOPE, pg.SPEAKER_OUT_OF_SCOPE_NEXT,
                    {"speaker": speaker, "tool": defn["name"], "reason": "out-of-scope",
                     "about": concerned or "everyone"})
        return None

    @staticmethod
    def _concerned_person(cell, defn, args, ctx):
        """Whose plan this call is about: the person argument, or the person the ref resolves to.
        None means the whole household — and it is deliberately NOT taken from the person an earlier
        turn settled on. A call that names nobody is answered about everybody (`list_people` lists the
        roster whatever was last discussed), so inheriting a settled person here would let a boundary
        drawn around one person unlock the rest of the household."""
        props = (defn.get("inputSchema") or {}).get("properties") or {}
        if "person" in props:
            person, _note = tools._person_or_default(cell, ctx, args, allow_clarify=False)
            return person
        if "ref" in props:
            item = tools.resolve_ref(cell, ctx, args.get("ref"))
            if item:
                return item["person"]
        return None


class _SpeakerRefused(Exception):
    """Raised when an answer that was read out of the cell goes beyond the speaker's boundary."""

    def __init__(self, message, next_step, event):
        super().__init__(message)
        self.message, self.next_step, self.event = message, next_step, event


def HANDLERS_RUN(defn, ctx, cell, args):
    return tools.HANDLERS[defn["name"]](ctx, cell, args)


def _count_error(name):
    with _lock:
        _metrics["errors"][name] = _metrics["errors"].get(name, 0) + 1


def _log_call(name, ms, rid):
    log.info(json.dumps({"evt": "tool", "tool": name, "ms": ms, "rid": rid, "ts": round(time.time(), 3)}))


def spoken_text(structured: dict) -> str:
    """Short, plain-language data for Alexa to speak (no ids, no JSON, < ~30 words)."""
    if "needs_clarification" in structured:
        return structured["question"]
    if "crisis" in structured:
        return structured["message"]
    if structured.get("error"):
        return structured["error"]
    if structured.get("needs_confirmation"):
        return structured.get("question") or "Please confirm that change."
    if "items" in structured and "person" in structured:
        items = structured["items"]
        if not items:
            return structured.get("empty", "Nothing is pending right now. ") + " " + pg.DISCLAIMER
        n = len(items)
        first = items[0]["text"][:120]
        more = f" plus {n-1} more" if n > 1 else ""
        att = structured.get("attribution", "")
        return f"For {structured.get('person') or 'the household'}: the {ordinal(1)} item — {first}{more}. {att}"
    if "slots" in structured:
        parts = [s for s in structured["slots"] if s["items"]]
        if not parts:
            return "Nothing is scheduled for today. " + pg.DISCLAIMER
        p0 = parts[0]
        dayparts = ("morning", "midday", "afternoon", "evening", "bedtime", "nightwaking",
                    "after food", "before food")
        when = p0["part_of_day"].lower().replace("_", " ")
        head = f"Today: {when} — " if when in dayparts else "Today: "
        return (head + p0["items"][0]["text"][:100]
                + (f", and {len(parts)-1} more times of day." if len(parts) > 1 else "."))
    if "new" in structured:
        if structured.get("message"):
            return structured["message"]
        return (f"Since the earlier visit: {len(structured['new'])} new, "
                f"{len(structured['stopped'])} stopped, {len(structured['changed'])} changed items.")
    if "done" in structured:
        return f"Today: {structured['done']} done, {structured['pending']} still pending. {structured.get('message','')}"
    if "quote" in structured:
        return f"Here is what was recorded on {structured.get('recorded_on','')}: {pg.mask_doses(structured['quote'])[:200]}"
    if "people" in structured:
        names = ", ".join(p["name"] for p in structured["people"]) or "nobody yet"
        return f"This household has {names}."
    if "capabilities" in structured:
        return ("CareLoop remembers what your clinician said. Ask what is next for someone, "
                "why an instruction was given, or what changed since the last visit. " + pg.DISCLAIMER)
    if "deleted" in structured:
        return "It has been removed from the plan."
    if "status" in structured and structured.get("status") == "rejected":
        return structured.get("message", "")
    if "needs_confirm" in structured and "items" not in structured:
        return ("That one still needs checking with the clinic. "
                if structured["needs_confirm"] else
                "Confirmed, so it no longer needs checking with the clinic.")
    if structured.get("status") == "done" and "remaining_count" in structured:
        left = structured.get("remaining_count") or 0
        head = "That was already marked done. " if structured.get("replayed") else "Done. "
        return head + (f"{left} still to do." if left else "Nothing else is pending.")
    if structured.get("status") in ("added", "deduped"):
        got = (structured.get("items") or [{}])[0]
        lead = "That was already on the plan: " if structured["status"] == "deduped" else "Added: "
        return lead + (got.get("text", "")[:110] or "It is on the plan now.") + "."
    if "open_flags" in structured:
        n = len(structured["open_flags"])
        return (f"{n} thing{'s' if n != 1 else ''} to ask about at the next visit, "
                f"starting with {structured['open_flags'][0]['text'][:110]}."
                if n else "Nothing is flagged to ask about; the plan is up to date.")
    return structured.get("disclaimer", pg.DISCLAIMER)


def ordinal(i):
    return tools.ORDINALS.get(i, str(i))


# ================================================================ HTTP layer
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CareLoop/6.0"
    _body_cache = None      # per-request; see _read_body

    def log_message(self, fmt, *a):  # quiet stdlib logging; we log structured lines instead
        pass

    # ------------------------------------------------------------- helpers
    def _send(self, status, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        hdrs = dict(self._security(ctype))
        hdrs.update(self._cors())
        hdrs.update(extra or {})
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _security(self, ctype: str) -> dict:
        """M12: a document that can show health data gets a strict policy, and an API response
        gets 'default-src none' so it can never become an attack surface of its own.
        'unsafe-inline' for script/style is a known, deliberate cost: the MCP App card is one
        self-contained file (the host may only accept a single HTML document), so a nonce or
        hash cannot be published. Everything else is closed off."""
        path = urllib.parse.urlsplit(self.path).path
        if ctype.startswith("text/html"):
            is_card = path in ("/card", "/card.html")
            # 'none' everywhere we can afford it. The Angular companion is the one built
            # document that declares <base href="/companion/">; with its base refused, the
            # relative asset URLs in that file resolve against the wrong directory and the
            # page loads unstyled. So that mount is allowed its own origin, which still
            # blocks the cross-origin base hijack the directive exists to stop.
            base = "base-uri 'self'" if path.startswith("/companion") else "base-uri 'none'"
            csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
                   f"connect-src 'self'; form-action 'self'; {base}; "
                   "object-src 'none'; frame-ancestors 'self'")
            # Microphone is only ever needed on the capture screen; nothing else may ask.
            mic = "microphone=(self)" if path.startswith("/companion") else "microphone=()"
            out = {"Content-Security-Policy": csp,
                   "Permissions-Policy": f"{mic}, camera=(), geolocation=(), payment=(), "
                                         f"usb=(), magnetometer=(), accelerometer=(), gyroscope=()",
                   "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer",
                   "X-Frame-Options": "SAMEORIGIN",
                   "Cross-Origin-Opener-Policy": "same-origin",
                   "Cross-Origin-Resource-Policy": "same-origin"}
            if is_card:
                out["Content-Security-Policy"] = csp.replace("default-src 'self'", "default-src 'none'", 1)
            if S.public_url.startswith("https://"):
                out["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            return out
        if ctype.split(";")[0].strip() in SUBRESOURCE_TYPES:
            return {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                    "Cache-Control": "no-store"}
        return {"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store"}

    def _cors(self):
        """Dev-time CORS: echo the origin only when it is on the allowlist, with credentials
        allowed so the companion session cookie works from the Angular/Vite dev servers."""
        o = self.headers.get("Origin")
        if not o or o not in S.allowed_origins:
            return {}
        return {"Access-Control-Allow-Origin": o,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers":
                    "Content-Type, Authorization, Accept, MCP-Protocol-Version, MCP-Session-Id",
                "Access-Control-Expose-Headers": "MCP-Session-Id, WWW-Authenticate",
                "Vary": "Origin"}

    def do_OPTIONS(self):
        extra = dict(self._cors())
        extra["Access-Control-Max-Age"] = "600"
        return self._send(204, b"", "text/plain", extra)

    def _json(self, status, obj, extra=None):
        self._send(status, json.dumps(obj).encode(), "application/json", extra)

    def _origin_ok(self):
        o = self.headers.get("Origin")
        if o is None:
            return True
        if o in S.allowed_origins or o.startswith("http://localhost") or o.startswith("http://127.0.0.1"):
            return True
        return False

    def _read_body(self):
        """The request body, taken at most once per request and then cached.

        do_POST calls this before routing, because a POST that is refused on auth, origin or
        Accept grounds still has its bytes sitting in the socket: this server is HTTP/1.1, so
        the browser reuses the connection and parses its *next* request out of those leftovers.
        Measured in headless Chrome: an unauthenticated POST /api/plan (401, body unread) was
        followed by GET /api/events answered with
        `501 Unsupported method ('{"person":null}GET')`.
        """
        if self._body_cache is not None:
            return self._body_cache
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            # Unreadable length means we cannot know how much to drain, so the connection
            # cannot be reused: close it instead of mis-aligning whatever comes next.
            self.close_connection = True
            self._body_cache = (b"", 400)
            return self._body_cache
        if n > S.max_body_bytes:
            # Drain (never buffer) so the client can read our 413 instead of a connection reset,
            # then close: the next request on this socket would be misaligned otherwise.
            self._drain(n)
            self.close_connection = True
            self._body_cache = (b"", 413)
            return self._body_cache
        body = self.rfile.read(n)
        self._body_cache = (body, None)
        return body, None

    def _drain(self, n, cap=8_000_000):
        left = min(n, cap)
        while left > 0:
            chunk = self.rfile.read(min(65536, left))
            if not chunk:
                break
            left -= len(chunk)

    def _token_row(self):
        return oauth.verify_bearer(self.headers.get("Authorization"))

    def _dev_identity(self):
        """CARELOOP_AUTH=off only: bind to the demo household so the simulator works
        with no credentials. Never active when auth is on."""
        g = store.global_db()
        hh = g.household_by_code("CASA01")
        if not hh:
            return None
        links = g.links(hh["hid"])
        link_id = links[0]["link_id"] if links else g.add_link(hh["hid"], "demo", "caregiver")
        return {"kind": "access", "scope": "mcp:tools", "hid": hh["hid"],
                "sub": link_id, "link_id": link_id, "audience": S.mcp_url,
                "expires_at": time.time() + 3600, "revoked": 0}

    # ------------------------------------------------------------- routes
    def do_GET(self):
        with _lock:
            _metrics["requests"] += 1
        path = urllib.parse.urlsplit(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "client_id" in q and ("code" in q or "access_token" in q):
            return self._json(400, {"error": "credentials_in_query_rejected"})
        if path == "/.well-known/oauth-protected-resource":
            return self._json(200, oauth.protected_resource_metadata(S.public_url))
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            return self._json(200, oauth.as_metadata(S.public_url))
        if path == "/oauth/authorize":
            v = oauth.authorize_get({k: (q.get(k) or [""])[0] for k in q})
            if v.get("error"):
                return self._send(400, f"<h1>Linking failed</h1><p>{pg.escape(v['error'])}</p>".encode())
            return self._send(200, oauth.login_page_html(v["params"]).encode())
        if path in ("/healthz",):
            return self._json(200, {"status": "ok"})
        if path in ("/readyz",):
            return self._json(200, {"status": "ready", "auth": S.auth_mode})
        if path == "/metrics":
            tool_ms = {k: percentiles(v) for k, v in _metrics["tools"].items()}
            slo = all((d["p95"] is None or d["p95"] < 500) for d in tool_ms.values())
            return self._json(200, {"counts": {"requests": _metrics["requests"], "errors": _metrics["errors"]},
                                    "latency_ms": tool_ms, "slo_p95_under_500ms": slo,
                                    "uptime_s": round(time.time() - _metrics["started"])})
        if path == "/judge" and not os.path.isfile(os.path.join(WEB_DIR, "simulator", "judge.html")):
            body = self._file("judge.html")
            return self._send(200, body.replace(b"__DATA__", json.dumps(self._judge_data()).encode()))
        if path == "/api/judge-data":
            return self._json(200, self._judge_data())
        if path == "/api/events":
            return self._api_events()
        if path == "/api/ledger":
            return self._api_ledger(q)
        if path == "/api/voice-scope":
            return self._api_voice_scope()
        if path == "/api/diagnostics":
            return self._api_diagnostics()
        if path == "/mcp":
            # Server-initiated SSE stream not supported; POST-only transport.
            return self._json(405, {"error": "method_not_allowed",
                                    "message": "Use POST /mcp (Streamable HTTP)."},
                              extra={"Allow": "POST"})
        if path == "/callback":
            return self._send(200, LINK_DONE_HTML.encode(), extra={"Cache-Control": "no-cache"})
        served = self._serve_dist(path)
        if served:
            status, body, ctype, extra = served
            return self._send(status, body, ctype, extra)
        return self._send(404, b"Page not found. Built frontends are served under /, /simulator, "
                              b"/companion and /judge - run `make dist` to build them.")

    # ------------------------------------------------------- static distribution
    def _dist_file(self, dist, rel):
        """Resolve a file inside a built bundle, refusing anything outside its root."""
        rel = (rel or "").split("?")[0].split("#")[0].strip("/")
        if not rel or ".." in rel or rel.startswith("/") or "\x00" in rel:
            return None
        root = os.path.realpath(WEB_DIR if dist == "." else os.path.join(WEB_DIR, dist))
        target = os.path.realpath(os.path.join(root, rel))
        if (target != root and not target.startswith(root + os.sep)) or not os.path.isfile(target):
            return None
        return target

    def _dist_response(self, dist, rel):
        target = self._dist_file(dist, rel)
        if not target:
            return None
        with open(target, "rb") as f:
            body = f.read()
        ext = os.path.splitext(target)[1].lower()
        hashed = any(m in target.replace("\\", "/") for m in HASHED_MARKERS)
        extra = {"Cache-Control": "public, max-age=31536000, immutable"
                 if hashed and ext != ".html" else "no-cache"}
        if ext == ".html":
            extra["X-Frame-Options"] = "SAMEORIGIN"
        return 200, body, MIME.get(ext, "application/octet-stream"), extra

    def _legacy(self, name):
        """Hand-written page directly under careloop/web (used before the bundles exist)."""
        return self._dist_response(".", name)

    def _serve_dist(self, path):
        if path in ("/", ""):
            return (self._dist_response("site", "index.html")
                    or self._dist_response("simulator", "index.html")
                    or self._legacy("simulator.html"))
        if path in ("/card", "/card.html"):
            return self._legacy("card.html")
        for prefix, (dist, entry) in MOUNTS.items():
            if path == prefix or path.startswith(prefix + "/"):
                rel = path[len(prefix):].strip("/")
                if rel:
                    got = self._dist_response(dist, rel)
                    if got:
                        return got
                return (self._dist_response(dist, entry)
                        or self._legacy("simulator.html" if dist == "simulator" else "companion.html"))
        if any(path == r or path.startswith(r + "/") for r in SITE_ROUTES):
            rel = path.strip("/")
            return (self._dist_response("site", rel + "/index.html")
                    or self._dist_response("site", rel + ".html")
                    or self._dist_response("site", "index.html")
                    or (self._legacy("privacy.html") if rel == "privacy" else None)
                    or (self._legacy("terms.html") if rel == "terms" else None))
        if path.startswith("/static/"):
            return (self._dist_response(".", path[len("/static/"):])
                    or self._dist_response("site", path[len("/static/"):]))
        for dist in DIST_DIRS:
            got = self._dist_response(dist, path)
            if got:
                return got
        return None


    def do_POST(self):
        with _lock:
            _metrics["requests"] += 1
        self._body_cache = None
        self._read_body()           # keep the connection aligned whatever route answers next
        path = urllib.parse.urlsplit(self.path).path
        if path == "/mcp":
            if not self._origin_ok():
                return self._json(403, {"error": "forbidden_origin"})
            accept = self.headers.get("Accept") or "*/*"
            if "application/json" not in accept and "*/*" not in accept and \
                    "text/event-stream" not in accept:
                return self._json(406, {"error": "not_acceptable"})
            tok = self._token_row()
            if S.auth_mode == "off" and tok is None:
                tok = self._dev_identity()
            if S.auth_mode in ("oauth", "token") and tok is None:
                if S.auth_mode == "oauth":
                    return self._json(401, {"error": "unauthorized"}, extra={
                        "WWW-Authenticate": f'Bearer resource_metadata="{S.public_url}'
                                            f'/.well-known/oauth-protected-resource"'})
            body, err = self._read_body()
            if err:
                return self._json(err, {"error": "body_too_large" if err == 413 else "bad_request"})
            mcp = MCP({"token": tok, "rid": _rid()})
            status, headers, out = mcp.handle(body, self.headers)
            extra = {"WWW-Authenticate": headers["WWW-Authenticate"]} if "WWW-Authenticate" in headers else {}
            if "MCP-Session-Id" in headers:
                extra["MCP-Session-Id"] = headers["MCP-Session-Id"]
            return self._send(status, out, "application/json", extra)
        if path == "/oauth/token":
            body, err = self._read_body()
            if err:
                return self._json(err, {"error": "body_too_large"})
            form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            if "client_secret" in form:
                return self._json(400, {"error": "invalid_request",
                                       "hint": "send credentials in the Basic header only"})
            res = oauth.token(form, self.headers.get("Authorization"))
            status = res.pop("status", 200)
            return self._json(status, res, extra={"Cache-Control": "no-store"})
        if path == "/oauth/authorize":
            q = {k: (v or [""])[0] for k, v in urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query).items()}
            body, err = self._read_body()
            if err:
                return self._json(err, {"error": "bad_request"})
            form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            res = oauth.authorize_login(q, form.get("code", "").upper(), form.get("pin", ""),
                                        form.get("consent") == "1")
            if res.get("error"):
                return self._send(200, oauth.login_page_html(q, res["error"]).encode())
            return self._redirect(res["redirect"])
        if path == "/api/create-household":
            body, _ = self._read_body()
            data = json.loads(body or b"{}")
            g = store.global_db()
            hid = g.create_household(data.get("code", ""), data.get("pin", "481542"))
            link = g.add_link(hid, data.get("name", "you"), "caregiver")
            sess = self._companion_login(hid, link)
            return self._json(200, {"household_code": data.get("code", "").upper(),
                                    "message": "Household ready. Keep the PIN private."},
                              extra={"Set-Cookie": sess})
        if path == "/api/login":
            body, _ = self._read_body()
            data = json.loads(body or b"{}")
            g = store.global_db()
            hh = g.household_by_code(data.get("code", ""))
            if not hh or not g.check_pin(hh["hid"], data.get("pin", "")):
                time.sleep(0.4)
                return self._json(401, {"error": "Invalid code or PIN."})
            links = g.links(hh["hid"])
            link = links[0]["link_id"] if links else g.add_link(hh["hid"], "owner", "caregiver")
            sess = self._companion_login(hh["hid"], link)
            return self._json(200, {"ok": True}, extra={"Set-Cookie": sess})
        if path == "/api/logout":
            return self._json(200, {"ok": True}, extra={"Set-Cookie": "cl_session=; Max-Age=0; Path=/"})
        if path == "/api/capture":
            ident = self._companion_identity() or (self._dev_identity() if S.auth_mode == "off" else None)
            if not ident:
                return self._json(401, {"error": "Please sign in to the companion page first."})
            body, err = self._read_body()
            if err:
                return self._json(err, {"error": "Transcript too large (1 MB limit)."})
            data = json.loads(body or b"{}")
            transcript = str(data.get("transcript", ""))[:1_000_000]
            if pg.is_crisis(transcript):
                return self._json(200, {"crisis": True, "message": pg.CRISIS_REDIRECT, "items": []})
            if pg.looks_like_injection(transcript):
                return self._json(400, {"error": "The transcript looks like instructions rather than a "
                                                 "visit conversation. Please paste the clinician's words."})
            child = pg.mentions_child(transcript)
            if child:
                return self._json(400, {"error": "CareLoop stores adult care plans only (policy §4)."})
            person = data.get("person") or "Household member"
            res = process_visit(transcript, S)
            cell = store.open_cell(ident["hid"])
            try:
                cell.add_person(person)
                vid, th = cell.record_visit(person, transcript, res["engine"], res["model"], res["cost"])
                added = 0
                made_refs = []
                for it in res["items"]:
                    out_item, status = cell.add_item(person=person, kind=it["kind"], sched_text=it["schedule"],
                                                     sched_json=it["sched_json"], quote=it["quote"][:600],
                                                     qstart=it["qstart"], qend=it["qend"], transcript_hash=th,
                                                     source=it["engine"], confidence=it["confidence"],
                                                     needs_confirm=it["needs_confirm"])
                    if status == "added":
                        added += 1
                        made_refs.append(out_item["ref"])
                # supersede detection (A5): same person+kind, new statement supersedes older pending one
                _apply_supersede(cell, person, [cell.get_item(r) for r in made_refs if cell.get_item(r)])
                result_items = _companion_items([cell.get_item(r) for r in made_refs if cell.get_item(r)])
            except store.StoreLocked:
                return self._json(503, {"error": "The care plan is briefly read-only — the visit was not "
                                                 "saved and nothing was changed. Please try again in a minute."})
            finally:
                cell.close()
            with _lock:
                _metrics["cost"] += res["cost"]
                if "escalated" in " ".join(res["notes"]):
                    _metrics["escalations"] += 1
            return self._json(200, {"visit_id": vid, "engine": res["engine"], "model": res["model"],
                                    "added": added, "notes": res["notes"], "cost_usd": res["cost"],
                                    "items": result_items})
        if path == "/api/voice-scope":
            return self._api_voice_scope_set()
        if path.startswith("/api/"):
            return self._api_write(path)
        return self._send(404, b"Not found")

    # --------------------------------------------------- companion helpers
    def _companion_login(self, hid, link_id):
        token = secrets.token_urlsafe(24)
        cell = store.open_cell(hid)
        cell.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('session', ?)",
                          (store.sha256_hex(token) + "|" + link_id,))
        cell.conn.commit()
        cell.close()
        return f"cl_session={token}; Path=/; HttpOnly; SameSite=Lax"

    def _companion_identity(self):
        cookie = self.headers.get("Cookie") or ""
        m = re.search(r"cl_session=([A-Za-z0-9_-]+)", cookie)
        if not m:
            return None
        token = m.group(1)
        for hid in _known_hids():
            cell = store.open_cell(hid, encrypt=False)
            r = cell.conn.execute("SELECT v FROM meta WHERE k='session'").fetchone()
            cell.close()
            if r and "|" in r["v"]:
                h, _, link_id = r["v"].partition("|")
                if hmac_compare(h, store.sha256_hex(token)):
                    lk = store.global_db().link(link_id)
                    if lk and lk["status"] == "linked":
                        return {"hid": lk["hid"], "role": lk["role"], "link_id": lk["link_id"]}
        return None

    def _api_write(self, path):
        ident = self._companion_identity()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        body, _ = self._read_body()
        data = json.loads(body or b"{}")
        cell = store.open_cell(ident["hid"])
        from . import authz as A
        principal = {"hid": ident["hid"], "role": ident["role"], "role_group": "HouseholdMembers"}
        try:
            if path == "/api/plan":
                dec = A.decide(principal, "ViewDose", {"hid": ident["hid"]}, {"surface": "companion"})
                items = cell.list_items(person=data.get("person") or None)
                visits = cell.visits()
                return self._json(200, {"items": _companion_items(items),
                                        "visits": [{"id": v["id"], "person": v["person"],
                                                    "hash": v["transcript_hash"][:16],
                                                    "at": v["created_at"]} for v in visits],
                                        "people": cell.people(),
                                        "ledger": cell.verify_ledger(),
                                        "full_doses_visible": bool(dec)})
            if path == "/api/mark":
                dec = A.decide(principal, "MarkDone", {"hid": ident["hid"]}, {"surface": "companion"})
                if not dec:
                    return self._json(403, {"error": "Not allowed."})
                res = cell.mark_done(data.get("ref", ""), who=data.get("who"),
                                     idem_key=data.get("idem"), version=data.get("version"))
                return self._json(200 if "error" not in res else 409, res)
            if path == "/api/confirm":
                return self._json(200, cell.confirm_item(data.get("ref", "")))
            if path == "/api/delete":
                dec = A.decide(principal, "DeleteItem", {"hid": ident["hid"]}, {"surface": "companion"})
                if not dec:
                    return self._json(403, {"error": "Only a caregiver can delete."})
                return self._json(200, cell.delete_item(data.get("ref", ""), confirm=bool(data.get("confirm"))))
            if path == "/api/add-person":
                return self._json(200, {"name": cell.add_person(data.get("name", ""),
                                                                data.get("role", "elder"),
                                                                data.get("aliases", []))})
            if path == "/api/export":
                dec = A.decide(principal, "ExportPlan", {"hid": ident["hid"]}, {"surface": "companion"})
                if not dec:
                    return self._json(403, {"error": "Not allowed."})
                md = export_sheet(cell, data.get("person"))
                return self._json(200, {"markdown": md,
                                        "ics": [timecraft_ics(i, cell) for i in
                                                cell.list_items(statuses=("pending",))
                                                if i["sched_json"].get("kind") == "ONCE"]})
            if path == "/api/erase":
                if not data.get("confirm"):
                    return self._json(400, {"error": "Type ERASE to confirm deleting everything."})
                receipt = cell.shred()
                # an erasure receipt is a claim about the disk; only 200 when it is true
                return self._json(200 if receipt.get("erased") else 409, receipt)
            return self._json(404, {"error": "not found"})
        except store.StoreLocked:
            return self._json(503, {"error": "CareLoop is briefly read-only; nothing was changed. "
                                             "Please try again in a minute."})
        finally:
            cell.close()

    # ------------------------------------------------------ the household's voice boundary
    def _api_voice_scope(self):
        ident = self._viewer()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        cell = store.open_cell(ident["hid"])
        try:
            return self._json(200, {"scope": cell.voice_scope(),
                                    "people": [p["name"] for p in cell.people()]})
        finally:
            cell.close()

    def _api_voice_scope_set(self):
        """The household states who may hear whose plan on the voice surface, and whether an
        instruction still waiting on the clinician may be read out to them. A boundary may only
        name people this household already has: it is a statement about reach, never health text."""
        ident = self._viewer()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        if ident.get("role") != "caregiver":
            return self._json(403, {"error": "Only a caregiver can decide who hears what."})
        body, _ = self._read_body()
        data = json.loads(body or b"{}")
        cell = store.open_cell(ident["hid"])
        try:
            names = [p["name"] for p in cell.people()]
            problems = _scope_problems(data.get("scope"), names)
            if problems:
                return self._json(400, {"error": "That boundary doesn't fit: "
                                                 + "; ".join(problems[:2]) + "."})
            return self._json(200, {"scope": cell.set_voice_scope(data.get("scope") or []),
                                    "people": names})
        except store.StoreLocked:
            return self._json(503, {"error": "CareLoop is briefly read-only; the boundary was not "
                                             "changed. Please try again in a minute."})
        finally:
            cell.close()

    # ------------------------------------------------- live sync, history, diagnostics
    def _viewer(self):
        """Companion identity, or the demo household when auth is off (the documented demo)."""
        ident = self._companion_identity()
        if ident:
            return ident
        if S.auth_mode == "off":
            dev = self._dev_identity()
            return {"hid": dev["hid"], "role": "caregiver", "link_id": dev["link_id"]} if dev else None
        return None

    def _client_gone(self):
        """Has the viewer hung up? A closed socket becomes readable and a peeked read then
        returns nothing. MSG_PEEK because a real pipelined request must stay in the buffer.
        Without this, a closed tab keeps its stream slot until the next write fails — up to
        a full ping interval of a seat that looks taken but is not."""
        try:
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _api_events(self):
        """M7 live sync: one Server-Sent Events stream per household, so a 'done' tapped on
        one phone shows up on a sibling's screen. Bounded deliberately: 8 concurrent viewers
        and 15 minutes per connection, because an open stream is a held resource."""
        ident = self._viewer()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        with _lock:
            if _streams["n"] >= _STREAM_MAX:
                return self._json(503, {"error": "Too many live viewers right now; "
                                                  "the plan itself still loads."})
            _streams["n"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in self._security("application/json").items():
            self.send_header(k, v)
        self.end_headers()
        cell = store.open_cell(ident["hid"])
        last, deadline, since_poll, since_ping = cell.verify_ledger()["events"], \
            time.time() + _STREAM_TTL, 0.0, 0.0
        try:
            self.wfile.write(f"event: hello\ndata: {json.dumps({'version': last})}\n\n".encode())
            self.wfile.flush()
            while True:
                if self.command == "HEAD" or time.time() > deadline or self._client_gone():
                    break
                time.sleep(_STREAM_TICK_S)
                since_poll += _STREAM_TICK_S
                since_ping += _STREAM_TICK_S
                if since_poll < _STREAM_POLL_S:
                    continue
                since_poll = 0.0
                st = cell.verify_ledger()
                if st["events"] != last:
                    last = st["events"]
                    payload = json.dumps({"type": "plan", "version": last,
                                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                          "verified": st["ok"]})
                    self.wfile.write(f"event: plan\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                    since_ping = 0.0
                elif since_ping >= _STREAM_PING_S:
                    since_ping = 0.0
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (OSError, ValueError):
            pass                       # the viewer closed the tab; nothing to say about that
        finally:
            cell.close()
            with _lock:
                _streams["n"] -= 1

    def _api_ledger(self, q):
        """M6 / N5 time travel: replay the chain up to a moment and answer 'what was the plan
        then?' from the ledger, not from a shadow table. Read-only by construction."""
        ident = self._viewer()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        until_raw = (q.get("until") or [""])[0]
        until = None
        if until_raw:
            try:
                until = float(until_raw)
            except ValueError:
                try:
                    import datetime as dt
                    until = dt.datetime.fromisoformat(until_raw).timestamp()
                except ValueError:
                    return self._json(400, {"error": "Tell me a date, like 2026-10-18T14:30."})
        cell = store.open_cell(ident["hid"])
        try:
            st = cell.verify_ledger()
            events, cutoff = cell.events_as_of(until if until is not None else time.time()), None
            for r in events:
                cutoff = r["ts"]
            state = cell.rebuild_state(until)
            items = []
            for ref, snap in state.items():
                items.append({"ref": ref, "person": snap.get("person"), "kind": snap.get("kind"),
                              "status": snap.get("status"), "text": snap.get("quote"),
                              "quote": snap.get("quote"), "qstart": snap.get("qstart"),
                              "qend": snap.get("qend"), "version": snap.get("version", 1),
                              "needs_confirm": bool(snap.get("confidence", 1) < 0.7),
                              "confidence": snap.get("confidence"),
                              "created_at": snap.get("ts")})
            return self._json(200, {
                "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff or time.time())),
                "requested_until": until, "verified": st["ok"],
                # `events` is the length of the chain at the moment asked, which is what a
                # scrubber moves along; `chain_events` is the whole chain as it stands now.
                "events": len(events), "chain_events": st["events"],
                "history": [{"seq": r["seq"], "at": r["ts"], "type": r["type"],
                             **{k: v for k, v in json.loads(cell.event_body(r) or "{}").items()
                                if k in ("person", "kind", "ref", "status")}} for r in events],
                "state": {"items": sorted(items, key=lambda i: (i["created_at"] or 0, i["ref"]))}})
        finally:
            cell.close()

    def _api_diagnostics(self):
        """M13 'report a bug': a bundle a reviewer can paste, with no health content in it.
        Counts, versions, timings and codes only — never a quote, a name or a dose."""
        ident = self._viewer()
        if not ident:
            return self._json(401, {"error": "Please sign in first."})
        from .tools import manifest_hash
        cell = store.open_cell(ident["hid"])
        try:
            st = cell.verify_ledger()
            kinds = {}
            for i in cell.list_items():
                kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        finally:
            cell.close()
        return self._json(200, {
            "product": SERVER_INFO, "protocol": list(PROTOCOLS),
            "manifest_hash": manifest_hash(), "auth_mode": S.auth_mode,
            "chaos": sorted(S.chaos), "chaos_unknown": S.chaos_unknown,
            "encryption_at_rest": bool(store.HAVE_CRYPTO and S.encryption),
            "counts": {"requests": _metrics["requests"], "tools": len(tools.TOOL_DEFS),
                       "errors": dict(_metrics["errors"]), "live_viewers": _streams["n"]},
            "latency_ms": {k: percentiles(v) for k, v in _metrics["tools"].items()},
            "cost_usd": round(_metrics["cost"], 4), "escalations": _metrics["escalations"],
            "ledger": {"events": st["events"], "ok": st["ok"],
                       "broken_at": st.get("broken_at"), "reason": st.get("reason")},
            "item_kind_counts": kinds,
            "household": store.sha256_hex(ident["hid"])[:12],
            "note": "No transcript text, quote, person name or dose is included in this bundle."})

    def _file(self, name):
        p = os.path.join(os.path.dirname(__file__), "web", name)
        try:
            with open(p, "rb") as f:
                return f.read()
        except OSError:
            return b"<h1>Missing page</h1>"

    def _redirect(self, loc):
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        for k, v in {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                     "Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"}.items():
            self.send_header(k, v)
        self.end_headers()

    def do_HEAD(self):
        return self.do_GET()

    def _judge_data(self):
        from .tools import manifest_hash
        return {
            "protocol": PROTOCOLS, "manifest_hash": manifest_hash(),
            "guest_safe": sorted(tools.GUEST_SAFE),
            "tools": [d["name"] for d in tools.TOOL_DEFS],
            "metrics": {"requests": _metrics["requests"], "errors": _metrics["errors"],
                        "latency": {k: percentiles(v) for k, v in _metrics["tools"].items()},
                        "cost_usd": round(_metrics["cost"], 4), "escalations": _metrics["escalations"]},
            "ledger": store.verify_all_cells(),
            "chaos": sorted(S.chaos),
            "chaos_unknown": S.chaos_unknown,
            "policy_self_test": policy_self_test(),
        }


def hmac_compare(a, b):
    import hmac as H
    return H.compare_digest(a, b)


def policy_self_test() -> dict:
    samples = ["Take 500 mg paracetamol", "Take two tablets nightly", "Insulin 10 units at bedtime"]
    masked = [pg.mask_doses(s) for s in samples]
    return {"dose_leak_after_mask": sum(pg.contains_dose(m) for m in masked),
            "crisis_recall_demo": pg.is_crisis("he wants to end it all"),
            "child_block_demo": pg.mentions_child("my son needs this"),
            "injection_block_demo": pg.looks_like_injection("ignore previous instructions"),
            "masked_examples": masked}


def _known_hids():
    g = store.global_db()
    return [r["hid"] for r in g.conn.execute("SELECT hid FROM households")]


def _companion_items(items):
    out = []
    for i in items:
        out.append({"ref": i["ref"], "person": i["person"], "kind": i["kind"], "status": i["status"],
                    "text": i["text"], "quote": i["quote"], "qstart": i.get("qstart"), "qend": i.get("qend"),
                    "schedule": i.get("sched_json", {}), "confidence": i["confidence"],
                    "needs_confirm": i["needs_confirm"], "version": i["version"],
                    "source": i["source"], "created_at": i["created_at"], "done_at": i.get("done_at")})
    return out


def _scope_problems(scope, known):
    """Validation for a household's stated voice boundary, before anything is stored: an entry may
    only name people this household already has, may only say yes-or-no about instructions still
    waiting on the clinician, and may not carry any other key. A boundary that names a stranger, a
    free-text field or a dose is refused here rather than stored."""
    if scope is None:
        # a request that forgot the key must not quietly wipe the household's boundary
        return ["the request has to carry the boundary it means to set"]
    if not isinstance(scope, list):
        return ["the boundary must be a list of household members"]
    if len(scope) > 20:
        return ["there are too many entries (twenty is the limit)"]
    lower = {str(n).strip().lower() for n in known or []}
    problems, seen = [], set()
    for entry in scope:
        if not isinstance(entry, dict):
            problems.append("each entry has to name a household member")
            continue
        speaker = str(entry.get("speaker", "")).strip()[:40]
        if speaker.lower() not in lower:
            problems.append(f"{speaker or 'an entry'} isn't someone in this household")
        elif speaker.lower() in seen:
            problems.append(f"{speaker} is given twice")
        seen.add(speaker.lower())
        may = entry.get("may_hear")
        if not isinstance(may, list) or not may:
            problems.append(f"{speaker or 'an entry'} needs at least one person they may hear")
        else:
            strangers = [str(m).strip() for m in may if str(m).strip().lower() not in lower]
            if strangers:
                problems.append("they may not hear " + ", ".join(strangers[:3])
                                + ", who " + ("is" if len(strangers) == 1 else "are")
                                + " not in this household")
        if not isinstance(entry.get("may_hear_pending", True), bool):
            problems.append(f"{speaker or 'an entry'} has to say yes or no about unconfirmed "
                            "instructions")
        for key in entry:
            if key not in ("speaker", "may_hear", "may_hear_pending"):
                problems.append(f"{key} isn't part of a boundary")
    return problems


def _apply_supersede(cell, person, extracted):
    """A5: a new medication/avoid statement for the same person supersedes the older pending one
    when both mention the same drug token."""
    for it in extracted:
        if it["kind"] not in ("medication", "avoid", "task", "monitor", "diet"):
            continue
        toks = {w for w in it["quote"].lower().replace(",", " ").split() if len(w) > 5}
        for old in cell.list_items(person=person, kinds=(it["kind"],), statuses=("pending",)):
            if old["ref"] == it.get("ref"):
                continue
            old_toks = {w for w in old["quote"].lower().replace(",", " ").split() if len(w) > 5}
            shared = toks & old_toks
            drug_like = [w for w in shared if w not in {"please", "medicine", "medicines", "tablet", "tablets",
                                                        "morning", "night", "still", "about"}]
            neg_change = ("stop" in it["quote"].lower() or "instead" in it["quote"].lower()
                          or "no longer" in it["quote"].lower())
            if drug_like and (neg_change or it["kind"] != old["kind"]):
                cell.supersede(old["ref"], it.get("ref") or "pending-new")
                break


def export_sheet(cell, person=None):
    import datetime as _dt
    items = cell.list_items(person=person)
    lines = [f"# CareLoop sheet — {person or 'all household members'}",
             f"_Generated {_dt.date.today().isoformat()}. Not medical advice — "
             f"this repeats what your clinician said._", ""]
    for i in items:
        lines.append(f"- **{i['kind']}** [{i['status']}] {i['text']}  \n  "
                     f"_“{i['quote']}” (recorded {_dt.datetime.fromtimestamp(i['created_at']):%d %b})_")
    return "\n".join(lines)


def timecraft_ics(item, cell):
    from . import timecraft as T
    return T.to_ics(item["person"], item["text"], item["sched_json"],
                    time.strftime("%Y-%m-%d", time.localtime(item["created_at"])), item["ref"])


# ================================================================ entry
def build_server(host=None, port=None):
    store.init(S)
    g = store.global_db()
    if not g.household_by_code("CASA01"):
        hid = g.create_household("CASA01", S.family_pin)
        g.add_link(hid, "demo-caregiver", "caregiver")
        # The documented demo household states its own voice boundary (README.md). It binds
        # only once that person exists in the cell, so capturing a visit for Mother is what makes
        # the boundary real; a household that sets none is answered as it always was.
        cell = store.open_cell(hid)
        try:
            cell.set_voice_scope([{"speaker": "Mother", "may_hear": ["Mother"],
                                   "may_hear_pending": False}])
        except store.StoreLocked:
            # a chaos run comes up read-only, and a missing demo default is not the fault being
            # demonstrated; the household can still state its boundary once writes are back
            pass
        finally:
            cell.close()
    httpd = ThreadingHTTPServer((host or S.host, port or S.port), Handler)
    return httpd


def main():  # pragma: no cover
    httpd = build_server()
    print(f"CareLoop on http://{S.host}:{S.port}  (simulator at /, companion at /companion, MCP at /mcp)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
