#!/usr/bin/env python3
"""make_addon — build the two judge-checklist deliverables that live *outside* the server:

    alexa/addon.json              the Alexa+ custom add-on / MCP connector manifest
    alexa/store-listing.json      the store listing (passes policy_guard.validate_store_listing)
    alexa/interaction-model.json  the voice intent model for the dose-free surface
    alexa/assets/*.png            the real icon + carousel files the listing references

Standard library only. Deterministic output (sorted keys, no timestamps) so `--check`
is meaningful in CI.

    python scripts/make_addon.py                 # offline: reads the careloop package
    python scripts/make_addon.py --offline
    python scripts/make_addon.py --from-server http://127.0.0.1:8213
    python scripts/make_addon.py --public-url https://care.example.org
    python scripts/make_addon.py --check         # drift + compliance gate (never writes)

Values come from the server contract itself: PROTOCOLS, SERVER_INFO, TOOL_DEFS, CARD_URI,
oauth.protected_resource_metadata / as_metadata and config.settings (TTLs, clients,
redirect URIs, body cap). No protocol version or tool name is hardcoded that the server
does not actually speak.
"""
import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSET_DIR = "alexa/assets"
DEFAULT_PUBLIC_URL = "http://localhost:8080"

ICON_SIZES = (64, 72, 88, 126, 180, 241)          # the six sizes validate_store_listing wants
CAROUSEL_W, CAROUSEL_H = 600, 900                  # plus one 600x900 carousel image
LOCALES = ("en-IN", "en-US", "en-GB")
MASK_TOKEN = "your prescribed amount"

# Versions to *probe* on a live server. A probe list, not a claim: only the versions the
# server accepts in the MCP-Protocol-Version header are written into the manifest.
PROTOCOL_PROBES = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")

BRAND_RX = re.compile(r"\b(amazon|alexa|echo|fire tv|ring)\b", re.IGNORECASE)  # mirrors policy_guard
DEV_URL_RX = re.compile(r"https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:\d+)?")
NUMERIC_ID_RX = re.compile(r"\b(?:i-[0-9a-f]{6,12}|[0-9a-f]{16,})\b", re.IGNORECASE)
# A spoken-delivery value: the speech format takes a rate or a volume as a percentage of the default.
PERCENT_RX = re.compile(r"\d{1,3}%")
SPOKEN_KEYS = {"body", "samples", "invocationName", "modelDescription", "shortAnswer",
               "spokenWords", "rule", "plainWords", "handling", "statement", "redirectPrompt",
               "welcomePrompt", "helpPrompt", "fallbackPrompt"}

BRAND_NOTE = ("Brand terms are the five policy_guard._BANNED_KEYWORDS look for. They do not appear in "
              "the developer's own copy: the surfaces are called 'your assistant' and 'the companion page'.")


def say(msg):
    print("WARNING  " + msg)


# ============================================================ palette and PNG artwork
def read_palette():
    """CareLoop palette, read from frontend/ui/src/tokens.css (:root, light theme)."""
    path = os.path.join(ROOT, "frontend", "ui", "src", "tokens.css")
    want = ("cl-bg", "cl-surface", "cl-surface-2", "cl-ink", "cl-line", "cl-accent", "cl-accent-soft",
            "cl-done", "cl-done-soft", "cl-info", "k-medication", "k-task", "k-monitor", "k-diet")
    fallback = {"cl-bg": "#f6f4ef", "cl-surface": "#ffffff", "cl-surface-2": "#efece4",
                "cl-ink": "#16202b", "cl-line": "#d8d3c8", "cl-accent": "#0d6e6b",
                "cl-accent-soft": "#d7ece9", "cl-done": "#2a6b3f", "cl-done-soft": "#dcede1",
                "cl-info": "#1b5a8a",
                "k-medication": "#1b5a8a", "k-task": "#0d6e6b", "k-monitor": "#8a5000", "k-diet": "#2a6b3f"}
    found = {}
    try:
        with open(path, encoding="utf-8") as f:
            css = f.read()
        block = re.search(r":root\s*\{(.*?)\}", css, re.S)
        found = dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", block.group(1) if block else css))
    except OSError:
        pass
    pal, missing = {}, []
    for key in want:
        hexv = found.get(key)
        if not hexv:
            missing.append(key)
            hexv = fallback[key]
        pal[key] = hex_to_rgb(hexv)
    return pal, missing


def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --- signed distance fields: enough geometry for flat, original, anti-aliased marks ---
def sd_round_box(px, py, cx, cy, hw, hh, r):
    qx = abs(px - cx) - hw + r
    qy = abs(py - cy) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def sd_circle(px, py, cx, cy, r):
    return math.hypot(px - cx, py - cy) - r


def sd_capsule(px, py, ax, ay, bx, by, r):
    abx, aby = bx - ax, by - ay
    denom = abx * abx + aby * aby or 1.0
    t = ((px - ax) * abx + (py - ay) * aby) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + abx * t), py - (ay + aby * t)) - r


def round_box(cx, cy, hw, hh, r):
    return ((cx - hw - 1.0, cy - hh - 1.0, cx + hw + 1.0, cy + hh + 1.0),
            lambda px, py: sd_round_box(px, py, cx, cy, hw, hh, r))


def disc(cx, cy, r):
    return ((cx - r - 1.0, cy - r - 1.0, cx + r + 1.0, cy + r + 1.0),
            lambda px, py: sd_circle(px, py, cx, cy, r))


def arc(cx, cy, rm, ht, deg0, deg1):
    """Open ring: a ring segment with rounded ends, measured in arc length."""
    a0, a1 = math.radians(deg0), math.radians(deg1)
    span = a1 - a0
    tau = 2.0 * math.pi

    def sdf(px, py):
        dx, dy = px - cx, py - cy
        radial = abs(math.hypot(dx, dy) - rm) - ht
        ang = (math.atan2(dy, dx) - a0) % tau
        along = (ang - span) * rm if ang > span else -min(ang, span - ang) * rm
        return math.hypot(max(radial, 0.0), max(along, 0.0)) + min(max(radial, along), 0.0)

    pad = rm + ht + 1.0
    return (cx - pad, cy - pad, cx + pad, cy + pad), sdf


class Canvas:
    """Painter's-algorithm RGBA canvas; coverage comes from the distance field."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[0.0] * (w * h) for _ in range(4)]

    def paint(self, layer, color, alpha=1.0):
        bbox, sdf = layer
        self.paint_sdf(bbox, color, sdf, alpha)

    def paint_sdf(self, bbox, color, sdf, alpha=1.0):
        x0, y0 = max(0, int(bbox[0])), max(0, int(bbox[1]))
        x1, y1 = min(self.w, int(math.ceil(bbox[2]))), min(self.h, int(math.ceil(bbox[3])))
        if x1 <= x0 or y1 <= y0:
            return
        r, g, b = color[0], color[1], color[2]
        a = (color[3] / 255.0 if len(color) > 3 else 1.0) * alpha
        cr, cg, cb, ca = self.ch
        for y in range(y0, y1):
            row = y * self.w
            py = y + 0.5
            for x in range(x0, x1):
                d = sdf(x + 0.5, py)
                if d >= 0.5:
                    continue
                f = (d + 0.5 if d > -0.5 else 1.0) * a
                if f <= 0.0:
                    continue
                i = row + x
                inv = 1.0 - f
                cr[i] = r * f + cr[i] * inv
                cg[i] = g * f + cg[i] * inv
                cb[i] = b * f + cb[i] * inv
                ca[i] = f + ca[i] * inv

    def rgba(self):
        """Colour channels accumulate in 0..255 weighted by coverage; divide by alpha."""
        cr, cg, cb, ca = self.ch
        out = bytearray(self.w * self.h * 4)
        for i in range(self.w * self.h):
            a = ca[i]
            if a <= 0.0:
                continue
            k = i * 4
            out[k] = min(255, int(round(cr[i] / a)))
            out[k + 1] = min(255, int(round(cg[i] / a)))
            out[k + 2] = min(255, int(round(cb[i] / a)))
            out[k + 3] = min(255, int(round(a * 255)))
        return bytes(out)


def png_bytes(w, h, rgba):
    """Minimal valid PNG: truecolour + alpha, filter 0, one IDAT, no text chunks."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    stride = w * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def png_ihdr(data):
    """Read the IHDR bytes back: the only proof a declared size is the real size."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG signature")
    if data[12:16] != b"IHDR":
        raise ValueError("first chunk is not IHDR")
    w, h = struct.unpack(">II", data[16:24])
    return w, h, data[24], data[25]


def draw_loop(cv, cx, cy, unit, pal):
    """Original CareLoop mark: an open care loop with a done tick inside. No brand mark."""
    cv.paint(disc(cx, cy, 0.235 * unit), pal["cl-accent-soft"] + (255,))
    bbox, sdf = arc(cx, cy, 0.34 * unit, 0.055 * unit, -35.0, 215.0)
    cv.paint((bbox, sdf), pal["cl-accent"] + (255,))
    ex = cx + 0.34 * unit * math.cos(math.radians(-35.0))
    ey = cy + 0.34 * unit * math.sin(math.radians(-35.0))
    cv.paint(disc(ex, ey, 0.058 * unit), pal["cl-accent"] + (255,))
    pts = [(cx - 0.13 * unit, cy + 0.01 * unit), (cx - 0.03 * unit, cy + 0.11 * unit),
           (cx + 0.15 * unit, cy - 0.11 * unit)]
    thick = 0.05 * unit
    for i in range(2):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        pad = math.hypot(bx - ax, by - ay) + thick + 1.0
        cv.paint_sdf((min(ax, bx) - pad, min(ay, by) - pad, max(ax, bx) + pad, max(ay, by) + pad),
                     pal["cl-done"] + (255,),
                     lambda px, py, ax=ax, ay=ay, bx=bx, by=by: sd_capsule(px, py, ax, ay, bx, by, thick))


def render_icon(size, pal):
    s = float(size)
    cv = Canvas(size, size)
    cv.paint(round_box(s / 2, s / 2, s / 2, s / 2, 0.22 * s), pal["cl-bg"] + (255,))
    draw_loop(cv, s / 2, s / 2, s * 0.86, pal)
    return png_bytes(size, size, cv.rgba())


def render_carousel(pal):
    w, h = float(CAROUSEL_W), float(CAROUSEL_H)
    cv = Canvas(CAROUSEL_W, CAROUSEL_H)
    cv.paint(round_box(w / 2, h / 2, w / 2, h / 2, 30.0), pal["cl-bg"] + (255,))
    cv.paint(round_box(w / 2, 830.0, w / 2 - 36.0, 40.0, 22.0), pal["cl-surface"] + (255,))
    # header: the mark plus two title bars
    cv.paint(round_box(w / 2, 170.0, w / 2 - 36.0, 110.0, 24.0), pal["cl-surface"] + (255,))
    draw_loop(cv, 118.0, 170.0, 150.0, pal)
    cv.paint(round_box(380.0, 138.0, 105.0, 13.0, 10.0), pal["cl-ink"] + (255,))
    cv.paint(round_box(345.0, 182.0, 70.0, 10.0, 8.0), pal["cl-line"] + (255,))
    cv.paint(round_box(345.0, 210.0, 96.0, 8.0, 8.0), pal["cl-accent-soft"] + (255,))
    # four plan rows: kind colour bar, two text bars, a status dot
    kinds = ("k-medication", "k-task", "k-monitor", "k-diet")
    for i, kind in enumerate(kinds):
        mid = 340.0 + i * 96.0
        cv.paint(round_box(w / 2, mid, w / 2 - 36.0, 38.0, 18.0), pal["cl-surface"] + (255,))
        cv.paint(round_box(86.0, mid, 6.0, 24.0, 6.0), pal[kind] + (255,))
        cv.paint(round_box(250.0, mid - 11.0, (95.0 - i * 9), 5.0, 5.0), pal["cl-ink"] + (255,))
        cv.paint(round_box(238.0, mid + 12.0, (60.0 + i * 14), 5.0, 5.0), pal["cl-line"] + (255,))
        dot = pal["cl-done-soft"] if i < 2 else pal["cl-surface-2"]
        cv.paint(disc(488.0, mid, 15.0), dot + (255,))
        if i < 2:
            for ax, ay, bx, by in ((481.0, mid, 487.0, mid + 6.0), (487.0, mid + 6.0, 497.0, mid - 6.0)):
                cv.paint_sdf((ax - 8.0, ay - 8.0, bx + 8.0, by + 8.0), pal["cl-done"] + (255,),
                             lambda px, py, ax=ax, ay=ay, bx=bx, by=by: sd_capsule(px, py, ax, ay, bx, by, 2.6))
    # bottom action pill
    cv.paint(round_box(w / 2, 700.0, 150.0, 32.0, 26.0), pal["cl-accent"] + (255,))
    cv.paint(round_box(w / 2, 700.0, 92.0, 8.0, 8.0), pal["cl-accent-soft"] + (255,))
    # footer: a note in the family's own words, kept with the plan
    cv.paint(round_box(86.0, 830.0, 6.0, 24.0, 6.0), pal["cl-info"] + (255,))
    cv.paint(round_box(250.0, 819.0, 120.0, 5.0, 5.0), pal["cl-ink"] + (255,))
    cv.paint(round_box(238.0, 842.0, 96.0, 5.0, 5.0), pal["cl-line"] + (255,))
    return png_bytes(CAROUSEL_W, CAROUSEL_H, cv.rgba())


def build_assets(pal):
    assets = {"icon-%dx%d.png" % (s, s): render_icon(s, pal) for s in ICON_SIZES}
    assets["carousel-%dx%d.png" % (CAROUSEL_W, CAROUSEL_H)] = render_carousel(pal)
    for name, data in sorted(assets.items()):
        w, h, depth, ctype = png_ihdr(data)
        declared = name.split("-")[1].split(".")[0]
        assert "%dx%d" % (w, h) == declared, "%s: IHDR %dx%d != declared %s" % (name, w, h, declared)
        assert depth == 8 and ctype == 6, "%s: expected 8-bit RGBA PNG" % name
    return assets


# ================================================================ source documents
def _in_process_mcp():
    from careloop import mcp_server
    return mcp_server.MCP({"token": {"scope": "mcp:tools", "kind": "access", "hid": None},
                           "rid": "make_addon"})


def rpc(caller, method, mid, params=None):
    payload = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params:
        payload["params"] = params
    _status, _headers, body = caller.handle(json.dumps(payload).encode(), {})
    return json.loads(body.decode())["result"]


def careloop_docs(public_url):
    """Offline mode: read the contract out of the careloop package, in process, no server."""
    os.environ["CARELOOP_PUBLIC_URL"] = public_url
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from careloop import mcp_server, oauth, tools
    from careloop.config import Settings

    cfg = Settings()
    caller = _in_process_mcp()
    init = rpc(caller, "initialize", 1, {"protocolVersion": mcp_server.PROTOCOLS[0],
                                         "clientInfo": {"name": "make_addon"}})
    listed = rpc(caller, "tools/list", 2)["tools"]
    resources = rpc(caller, "resources/list", 3)["resources"]
    return {
        "mode": "offline", "observedFrom": None,
        "publicUrl": cfg.public_url, "endpoint": cfg.mcp_url,
        "protocols": list(mcp_server.PROTOCOLS), "probeResults": None,
        "readiness": {"status": "ready", "auth": cfg.auth_mode},
        "negotiated": init["protocolVersion"], "serverInfo": init["serverInfo"],
        "capabilities": init["capabilities"], "resources": resources, "tools": listed,
        "guestSafe": sorted(tools.GUEST_SAFE), "uiTools": sorted(tools.UI_TOOLS),
        "cardUri": tools.CARD_URI,
        "actions": {d["name"]: d.get("action") for d in tools.TOOL_DEFS},
        "annotations": {d["name"]: d.get("annotations", {}) for d in tools.TOOL_DEFS},
        "prm": oauth.protected_resource_metadata(cfg.public_url),
        "asMetadata": oauth.as_metadata(cfg.public_url),
        "cfg": {"accessTtl": cfg.access_ttl, "serviceTtl": cfg.service_ttl, "refreshTtl": cfg.refresh_ttl,
                "clientId": cfg.client_id, "alexaClientId": cfg.alexa_client_id,
                "redirectUris": sorted(cfg.redirect_uris), "maxBodyBytes": cfg.max_body_bytes,
                "authMode": cfg.auth_mode},
    }


def live_docs(base_url):
    """Live mode: derive endpoint, protocol versions, scopes and tools from the running server."""
    base = base_url.rstrip("/")
    opener = urllib.request.build_opener()

    def get(path):
        req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
        return json.loads(opener.open(req, timeout=10).read().decode())

    def post(payload, sid=None, proto=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if sid:
            headers["MCP-Session-Id"] = sid
        if proto:
            headers["MCP-Protocol-Version"] = proto
        req = urllib.request.Request(base + "/mcp", data=json.dumps(payload).encode(), headers=headers)
        try:
            resp = opener.open(req, timeout=10)
            return resp.status, dict(resp.headers), json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, dict(e.headers), json.loads(raw.decode() or "{}")
            except Exception:
                return e.code, dict(e.headers), {}

    prm = get("/.well-known/oauth-protected-resource")
    try:
        asmd = get("/.well-known/oauth-authorization-server")
    except (urllib.error.URLError, KeyError):
        asmd = get("/.well-known/openid-configuration")
    try:
        ready = get("/readyz")
    except (urllib.error.URLError, json.JSONDecodeError):
        ready = {}

    status, headers, init = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-11-25",
                                            "clientInfo": {"name": "make_addon"}}})
    if status != 200 or "result" not in init:
        raise SystemExit("make_addon: POST %s/mcp initialize failed (HTTP %s) - is the server running "
                         "and reachable without a token (CARELOOP_AUTH=off)?" % (base, status))
    sid = headers.get("MCP-Session-Id") or headers.get("mcp-session-id")
    negotiated = init["result"]["protocolVersion"]

    probes = {}
    for v in PROTOCOL_PROBES:
        code, _h, _b = post({"jsonrpc": "2.0", "id": 90, "method": "ping"}, sid=sid, proto=v)
        probes[v] = "supported" if code == 200 else ("rejected" if code == 400 else "http-%d" % code)
    supported = tuple(v for v in PROTOCOL_PROBES if probes[v] == "supported")
    if not supported:
        supported = (negotiated,)

    status, _h, tl = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid, proto=negotiated)
    listed = tl.get("result", {}).get("tools", []) if status == 200 else []
    if not listed:
        raise SystemExit("make_addon: tools/list returned nothing from %s (HTTP %s) - the manifest "
                         "cannot be honest without the live tool contract." % (base, status))
    status, _h, rl = post({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, sid=sid, proto=negotiated)
    resources = rl.get("result", {}).get("resources", []) if status == 200 else []

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from careloop import tools as cl_tools
    from careloop.config import Settings

    cfg = Settings()
    endpoint = prm["resource"]
    public = endpoint[:-len("/mcp")] if endpoint.endswith("/mcp") else cfg.public_url
    return {
        "mode": "live", "observedFrom": base,
        "publicUrl": public, "endpoint": endpoint,
        "protocols": list(supported), "probeResults": probes, "readiness": ready,
        "negotiated": negotiated, "serverInfo": init["result"].get("serverInfo", {}),
        "capabilities": init["result"].get("capabilities", {}), "resources": resources, "tools": listed,
        "guestSafe": sorted(cl_tools.GUEST_SAFE), "uiTools": sorted(cl_tools.UI_TOOLS),
        "cardUri": resources[0]["uri"] if resources else None,
        "actions": {d["name"]: d.get("action") for d in cl_tools.TOOL_DEFS},
        "annotations": {d["name"]: d.get("annotations", {}) for d in cl_tools.TOOL_DEFS},
        "prm": prm, "asMetadata": asmd,
        "cfg": {"accessTtl": cfg.access_ttl, "serviceTtl": cfg.service_ttl, "refreshTtl": cfg.refresh_ttl,
                "clientId": cfg.client_id, "alexaClientId": cfg.alexa_client_id,
                "redirectUris": sorted(cfg.redirect_uris), "maxBodyBytes": cfg.max_body_bytes,
                "authMode": cfg.auth_mode},
    }


def tool_contract_hash(tools_list):
    """Same canonicalisation as careloop.tools.manifest_hash, over whatever list we were given."""
    core = [{"name": t["name"], "description": t.get("description", ""),
             "inputSchema": t.get("inputSchema", {}), "outputSchema": t.get("outputSchema", {})}
            for t in tools_list]
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


# ============================================================= voice intent contract
# One intent per tool the voice surface is allowed to use, plus the device-local intents below that call
# nothing. `tool` is a machine field and
# is never spoken. The five custom slot types are declared once below so no intent can invent a slot
# the model does not have; a slot names the argument it fills with `"arg"`, and `"arg": None` means
# the slot is collected so the assistant can speak and confirm correctly but is deliberately NOT sent,
# because the contract has no such argument. `routing_findings` cross-checks every mapping against the
# generated tool contracts, so neither side can drift from the other silently.
PersonSlot = {"name": "Person", "type": "CareLoopPerson", "prompt": "ClarifyPersonPrompt", "arg": "person"}
SectionSlot = {"name": "Section", "type": "CareLoopPlanSection", "arg": "kind"}
ItemPositionSlot = {"name": "ItemPosition", "type": "CareLoopItemPosition", "required": True,
                    "prompt": "ElicitItemPrompt", "arg": "ref"}
NoteTextSlot = {"name": "NoteText", "type": "CareLoopNoteText", "required": True, "capture": "listenOnce",
                "wildcard": True, "prompt": "ElicitNotePrompt", "arg": "text"}
DeliverySlot = {"name": "Delivery", "type": "CareLoopDeliveryStyle", "required": True,
                "prompt": "ElicitDeliveryPrompt", "arg": None}

VOICE_INTENTS = [
    {"intent": "WhatsMyPlanIntent", "tool": "get_care_plan",
     "slots": [PersonSlot, SectionSlot],
     "answer": "Name the person, say the most immediate item in the clinician's own words, then say how "
               "many more there are and offer them on screen."},
    {"intent": "TodaysPlanIntent", "tool": "todays_plan",
     "slots": [PersonSlot],
     "answer": "Give the earliest part of the day with its first item, then say how many other times of "
               "day have something left."},
    {"intent": "MarkDoneIntent", "tool": "mark_done",
     # mark_done names the person `who`, not `person`: it is a different contract from the read tools.
     "slots": [ItemPositionSlot, dict(PersonSlot, arg="who")],
     "answer": "Say it is recorded, then say how much is still pending."},
    {"intent": "ExplainInstructionIntent", "tool": "explain_instruction",
     "slots": [ItemPositionSlot],
     "answer": "Read back the clinician's own recorded words for that item and the date they were "
               "recorded. Do not supply a reason the recording does not contain: if it does not say why, "
               "say that it does not say."},
    {"intent": "AddVerbalNoteIntent", "tool": "add_note",
     "slots": [NoteTextSlot, PersonSlot],
     "answer": "Repeat the sentence back exactly as stored and say it now travels with the plan.",
     "preChecks": ["crisis", "child-safety", "prompt-injection"]},
    {"intent": "ListPeopleIntent", "tool": "list_people", "slots": [],
     "answer": "Say the names the household plans cover."},
    {"intent": "WhatChangedIntent", "tool": "what_changed",
     "slots": [PersonSlot],
     "answer": "Give the counts of new, stopped and changed items and the visit they were compared with."},
    {"intent": "DailyDigestIntent", "tool": "daily_digest",
     "slots": [PersonSlot],
     "answer": "Say how much of today is done and how much is still pending, in one sentence."},
    {"intent": "PrepareVisitQuestionsIntent", "tool": "prepare_next_visit",
     "slots": [PersonSlot],
     "answer": "Read out the items still waiting on the clinician, and say they are only questions to ask."},
    {"intent": "RemoveInstructionIntent", "tool": "delete_instruction",
     # delete_instruction takes no person: the name is spoken back for the read-only confirmation and is
     # never sent. The yes/no answer lands on `confirm`, which the device sets from the confirmation.
     "slots": [ItemPositionSlot, dict(PersonSlot, arg=None)],
     "confirmArgument": "confirm",
     "answer": "Read the item back and ask for a clear yes before anything goes. Never remove on the "
               "first request.",
     "confirmationRequired": True},
]
ABOUT_INTENT = {"intent": "AboutCareLoopIntent", "tool": "about_careloop", "slots": [],
                "answer": "Say what CareLoop can help with in one or two sentences, and that it only "
                          "repeats what a clinician recorded.",
                "guestSafe": True}

# Two device-local intents. They change *how* an answer is spoken and *who the device is answering*,
# and they route to no tool at all: an intent with no tool physically cannot write to a care plan, and
# `routing_findings` refuses the declaration the moment a slot argument, a confirmation or a required
# tool argument is added to it. Every rate, volume, word ceiling and sentence they use lives in
# `presentation.delivery` and the prompt catalog, so a device reads them from the artifact instead of
# inventing one. Neither intent may be held open for a yes, and neither arms the device-initiated turn.
DEVICE_LOCAL_INTENTS = [
    {"intent": "AdjustDeliveryIntent", "deviceLocal": True, "slots": [DeliverySlot],
     "answer": "Say which way the delivery changed, using the line the artifact names for it, then speak "
               "again the answer this customer already had. Never say anything about the plan that the "
               "held answer did not already say."},
    {"intent": "IdentifySpeakerIntent", "deviceLocal": True, "slots": [dict(PersonSlot, arg=None,
                                                                            required=True)],
     "answer": "Say that the device will answer as that household member for the rest of this session and "
               "that what may be read out is bounded by what the household lets that member hear. Name no "
               "item, no amount and no flag from any plan."},
]

ALL_INTENTS = VOICE_INTENTS + [ABOUT_INTENT] + DEVICE_LOCAL_INTENTS

PEOPLE_VALUES = ("mother", "mum", "mom", "my wife", "my mum", "father", "dad", "papa", "my husband",
                 "grandmother", "grandfather", "nan", "grandma", "grandad", "aunt", "uncle",
                 "my mother in law", "my father in law", "my partner", "my sister", "my brother",
                 "the nurse", "household member")
POSITION_VALUES = ("first", "second", "third", "fourth", "fifth")
# What a family says -> the `kind` the server's own contract accepts. Six kinds, fourteen ways of
# asking: a synonym that has no server kind would be a value the tool must reject, so none is listed.
SECTION_VALUES = (("medicine", "medication"), ("tablet", "medication"), ("pill", "medication"),
                  ("task", "task"), ("thing to do", "task"),
                  ("avoid", "avoid"), ("not to do", "avoid"),
                  ("food", "diet"), ("diet", "diet"),
                  ("check up", "monitor"), ("blood pressure", "monitor"), ("reading", "monitor"),
                  ("visit", "appointment"), ("appointment", "appointment"))
# What a family says about *how* something is spoken -> the style id `presentation.delivery.styles`
# carries. Five styles, and the only thing a style changes is rate, volume and length: no style can add,
# change or remove anything, because the intent that collects it routes to no tool.
DELIVERY_VALUES = (("slower", "slower"), ("more slowly", "slower"), ("at a slower pace", "slower"),
                   ("say that slow", "slower"),
                   ("louder", "loud"), ("more loudly", "loud"), ("speak up", "loud"),
                   ("softer", "soft"), ("quieter", "soft"), ("in a low voice", "soft"),
                   ("keep it short", "brief"), ("the short version", "brief"),
                   ("just the essentials", "brief"), ("shorter", "brief"),
                   ("the usual way", "normal"), ("normally", "normal"), ("back to normal", "normal"))


def build_interaction_model(docs):
    pg = policy_guard()          # the voice-profile keys are the service's, not this script's
    catalog = [
        ("WelcomePrompt", ["CareLoop is ready. Ask what is next for someone, why an instruction was given, "
                           "or say what you want remembered."]),
        ("HelpPrompt", ["I only repeat what your clinician recorded for this household. I can tell you what "
                        "is next, why something was said, what changed, and what is still to do."]),
        ("FallbackPrompt", ["I didn't catch that. You can ask what is next for someone, or what changed "
                            "since the last visit."]),
        ("ClarifyPersonPrompt", ["Who should I ask about? Say the name or the relation, like my mother."]),
        ("ElicitItemPrompt", ["Which one on the list? Say the first, the second, the third, the fourth or "
                              "the fifth."]),
        ("ElicitNotePrompt", ["What should I remember? Say the sentence in your own words and I keep it "
                              "exactly as you say it."]),
        ("LinkHouseholdPrompt", [docs["guestNotice"]]),
        ("NoPlanPrompt", ["I don't have a recording about that. Ask for the plan again, then refer to an "
                          "item by its place, like the second one."]),
        ("NotAllowedPrompt", ["Your household role can't do that by voice. You can do it on the CareLoop "
                              "companion page."]),
        ("ConfirmRemovePrompt", ["Just to check. Should I really remove the {ItemPosition} item from this "
                                 "household's plan? Say yes to remove it, or no to keep it."]),
        ("RemovedPrompt", ["It has been removed from the plan."]),
        ("KeptPrompt", ["Nothing was removed. It stays in the plan."]),
        ("ReminderPrompt", ["CareLoop is reading today's plan back to you."]),
        ("ElicitDeliveryPrompt", ["How should I say it? You can ask for it slower, louder, softer, shorter "
                                  "or the usual way."]),
        ("DeliverySlowerPrompt", ["Slower now."]),
        ("DeliveryLoudPrompt", ["Louder now."]),
        ("DeliverySoftPrompt", ["Softer now."]),
        ("DeliveryBriefPrompt", ["The short version."]),
        ("DeliveryNormalPrompt", ["Back to the usual pace and length."]),
        ("SpeakerTakenPrompt", ["For this session I will answer as {Person}. What I can read out is "
                                "bounded by what your household lets {Person} hear."]),
        ("AmountRedirectPrompt", ["I don't say amounts out loud. Open the CareLoop companion page for this "
                                  "household to read the exact words your clinician recorded, amounts "
                                  "included."]),
        ("CrisisPrompt", [docs["crisisRedirect"]]),
    ]
    prompts = [{"id": pid, "type": "PlainText", "maxSpokenSeconds": 30, "body": list(body)}
               for pid, body in catalog]

    samples = sample_utterances()
    card_intents = {i["intent"] for i in ALL_INTENTS
                    if i.get("tool") in ("get_care_plan", "todays_plan", "list_people")
                    and i.get("tool") in docs["uiTools"]}
    intents = []
    for spec in ALL_INTENTS:
        slots = []
        for slot_spec in spec["slots"]:
            slot = {"name": slot_spec["name"], "type": slot_spec["type"],
                    "required": bool(slot_spec.get("required"))}
            if slot_spec.get("capture"):
                slot["capture"] = slot_spec["capture"]
            if slot_spec.get("prompt"):
                # The question the skill asks when this slot is still empty. Declared per slot so a
                # chained elicitation never invents a sentence.
                slot["prompt"] = slot_spec["prompt"]
            if slot_spec.get("wildcard"):
                slot["wildcard"] = True
            slots.append(slot)
        if spec.get("deviceLocal"):
            # No tool, so no argument can be sent and nothing can be written. The declaration says so in
            # as many words, because a reader of the artifact must not have to infer it from an absence.
            routing = {"type": "device-local", "tool": None, "surface": "voice",
                       "writesAnything": False}
        else:
            routing = {"type": "mcp-tool", "tool": spec["tool"], "surface": "voice",
                       "slotArguments": {s["name"]: s["arg"] for s in spec["slots"] if s.get("arg")}}
        intent = {
            "name": spec["intent"],
            "samples": samples[spec["intent"]],
            "slots": slots,
            "routing": routing,
            "answerGuidance": {"maxSpokenSeconds": 30, "oneItemThenCard": True,
                               "say": spec["answer"]},
            "response": {"directives": ([{"type": "render-mcp-app-card", "resourceUri": docs["cardUri"]}]
                                        if spec["intent"] in card_intents else [])},
        }
        if spec.get("confirmArgument"):
            # The argument the yes/no answer lands on. Declared here so a device reads it from the
            # model instead of hard-coding a field name that the contract may not have.
            intent["routing"]["confirmArgument"] = spec["confirmArgument"]
        if spec.get("preChecks"):
            intent["preChecks"] = spec["preChecks"]
        if spec.get("guestSafe"):
            intent["answerWhenUnlinked"] = "LinkHouseholdPrompt"
        intents.append(intent)

    types = [
        {"name": "CareLoopPerson", "resolution": "household aliases",
         "note": "Who the plan is about, said the way families say it. Resolved against the household's own "
                 "people; an unclear match comes back as a question, never a guess.",
         "values": [{"id": v, "name": {"value": v}} for v in PEOPLE_VALUES]},
        {"name": "CareLoopItemPosition", "resolution": "ordinal words the server can resolve",
         "note": "Which item from the plan just read out, by its place in the list. Only places the server "
                 "resolves are offered, so no item identifier is ever spoken or collected.",
         "values": [{"id": v, "name": {"value": v}} for v in POSITION_VALUES]},
        {"name": "CareLoopPlanSection", "resolution": "closed kind vocabulary",
         "note": "A part of the plan. The value is what a family says and the id is the kind the server's "
                 "own contract names, so a synonym can never be sent as an argument the tool would reject.",
         "values": [{"id": kind, "name": {"value": said}} for said, kind in SECTION_VALUES]},
        {"name": "CareLoopNoteText", "resolution": "free text, captured once",
         "note": "The caller's own words for something the family was told verbally. CareLoop stores them as "
                 "spoken and adds nothing of its own.", "wildcard": True, "values": []},
        {"name": "CareLoopDeliveryStyle", "resolution": "closed delivery vocabulary",
         "note": "How an answer is spoken, never what it says. Each value is a style id that "
                 "`presentation.delivery.styles` maps to a rate, a volume and a word ceiling, so the device "
                 "reads the numbers from this artifact instead of hard-coding them, and a tighter answer is "
                 "still the same sentence cut to length rather than a new sentence.",
         "values": [{"id": style, "name": {"value": said}} for said, style in DELIVERY_VALUES]},
    ]

    model = {
        "schemaVersion": "careloop-interaction-model/1",
        "generatedBy": "scripts/make_addon.py",
        "surface": "voice",
        "endpoint": {"mcpUri": docs["endpoint"], "protocolVersions": list(docs["protocols"]),
                     "preferredProtocolVersion": docs["negotiated"], "cardResourceUri": docs["cardUri"],
                     "authScope": "mcp:tools"},
        "languageModel": {
            "invocationName": "care loop",
            "modelDescription": ("Voice surface for one household's recorded adult care plan. Dose-free by "
                                 "design: no intent collects or returns a medicine amount, and nothing here "
                                 "suggests a medicine, a change or a conclusion."),
            "intents": intents,
            "types": types,
        },
        "dialog": {
            "delegation": {"strategy": {"type": "skill"}},
            "confirmationRequired": {"RemoveInstructionIntent": {
                "prompt": "ConfirmRemovePrompt", "type": "PLAIN", "defaultValue": "ON"}},
            "confirmationResponses": {"RemoveInstructionIntent": {
                "accept": {"renderPrompt": "RemovedPrompt", "toolConfirm": True},
                "reject": {"renderPrompt": "KeptPrompt", "toolConfirm": False}}},
            "eloquenceStyle": "concise",
        },
        "proactive": {
            "note": ("One device-initiated turn, for the thing a caregiver cannot remember to ask "
                     "about: the plan they had just looked at. The skill asks the device to start a "
                     "connection and the device resumes its own session when the interval declared "
                     "here has passed — the Connections resume path, with a connection name that is "
                     "ours and is labelled as such, because a simulated device has no platform "
                     "routine to read. What the resumed session says is the same read-only tool call "
                     "the customer could have made, and the dose gate is in front of it like every "
                     "other voice answer. The device writes nothing on this path."),
            "reminder": {
                # Which answer arms the connection: the intent, and so the read-only tool behind it.
                "intent": "TodaysPlanIntent",
                "prompt": "ReminderPrompt",
                "connectionUri": "connection://CareLoop.CareReminder/1",
                # ask_sdk_model.interfaces.connections.on_completion.OnCompletion: the other value,
                # SEND_ERRORS_ONLY, is never resumed, so a reminder would never be spoken.
                "onCompletion": "RESUME_SESSION",
                "resumeAfterSeconds": 8,
                "requestType": "SessionResumedRequest",
                "causeType": "ConnectionCompleted",
                "directiveType": "Connections.StartConnection",
                "writesAnything": False,
            },
        },
        "presentation": {
            "note": ("How an answer is spoken is an accessibility setting, not a care decision. The styles "
                     "below are the whole vocabulary a customer can ask for, and each one carries the rate, "
                     "the volume and the word ceiling the device applies — in SSML, to the sentence the "
                     "server already returned. A shorter answer is the same sentence cut to length, never a "
                     "new sentence, because this repo does not write health text. The intent that collects "
                     "a style routes to no tool, so asking to be understood better cannot change a plan."),
            "delivery": {
                "intent": "AdjustDeliveryIntent",
                "slot": "Delivery",
                "defaultStyle": "normal",
                # The style is applied to the answer already in the customer's hand: the device says the
                # same thing again, differently, instead of fetching something new.
                "appliesToHeldAnswer": True,
                "writesAnything": False,
                "styles": {
                    "slower": {"rate": "80%", "volume": None, "maxSpokenWords": 48,
                               "prompt": "DeliverySlowerPrompt"},
                    "loud": {"rate": "100%", "volume": "140%", "maxSpokenWords": 48,
                             "prompt": "DeliveryLoudPrompt"},
                    "soft": {"rate": "100%", "volume": "60%", "maxSpokenWords": 48,
                             "prompt": "DeliverySoftPrompt"},
                    "brief": {"rate": "100%", "volume": None, "maxSpokenWords": 18,
                              "prompt": "DeliveryBriefPrompt"},
                    "normal": {"rate": "100%", "volume": "100%", "maxSpokenWords": 48,
                               "prompt": "DeliveryNormalPrompt"},
                },
            },
        },
        "voiceProfile": {
            "note": ("Whose voice the device is answering for, declared here so neither device invents a "
                     "field name. The identification is SIMULATED: a browser device and a scripted driver "
                     "cannot hear a voice, so the customer (or the script) states who is speaking and the "
                     "device passes that statement to the server as the MCP request's own _meta. The "
                     "server, not the device, decides what that member may hear, from the scope the "
                     "household itself stored; an out-of-scope question is refused and the refusal is "
                     "recorded. On a real deployment the same decision would be made against the verified "
                     "identity behind the linked account, which is why the device's claim is labelled as a "
                     "claim and the profile value names itself as simulated. Nothing on this path writes, "
                     "and no dose rides it: the refusal is one of the artifact's own sentences."),
            "assertion": {
                "intent": "IdentifySpeakerIntent",
                "slot": "Person",
                "prompt": "SpeakerTakenPrompt",
                # generated FROM the service's own constants (careloop/policy_guard.py), so a device
                # built from this artifact and the server that answers it cannot disagree on a key
                "speakerMetaKey": pg.SPEAKER_META_KEY,
                "profileMetaKey": pg.VOICE_PROFILE_META_KEY,
                "profileValue": pg.SIMULATED_PROFILE_VALUE,
                "simulated": True,
                "deviceDecidesScope": False,
                "writesAnything": False,
                "sessionScopedOnly": True,
            },
        },
        "promptCatalog": {
            "note": "Prompts are plain sentences for speaking. They never name an internal field, a code, "
                    "an identifier or a technical format, and every one fits inside maxSpokenSeconds.",
            "prompts": prompts,
        },
        "answerGuidance": {
            "maxSpokenSeconds": 30,
            "maxSpokenWords": 48,
            "rule": "One item out loud, then offer the rest on screen. Long lists go on the card, never "
                    "into speech.",
            "plainWords": "Speak the clinician's recorded words rather than a summary of them.",
            "attribution": "Every care answer says it came from your clinician and when it was recorded.",
            "silenceTimeoutSeconds": 8,
            "repromptAfterSeconds": 12,
        },
        "guardrails": {
            "doseFree": {
                "statement": ("Nothing spoken on this surface may carry a medicine amount. Amounts are "
                              "replaced with 'your prescribed amount' before an answer is spoken, and the "
                              "full record is only on the household companion page."),
                "mechanism": ("every outbound field is masked, then checked again; a residual amount is an "
                              "incident that is counted, not a retry"),
                "collectsAnAmount": False,
                "numericAmountSlots": False,
                "redirectPrompt": "AmountRedirectPrompt",
            },
            "nothingInvented": {
                "statement": ("The assistant never suggests, changes, stops or explains the reason for an "
                              "instruction. It repeats what was recorded, and when it has no recording it "
                              "says so."),
                "noAdviceGiven": True,
                "unsureIsFlagged": ("anything unclear is returned flagged for the clinician instead of "
                                    "smoothed over"),
                "prompt": "NoPlanPrompt",
            },
            "neverAsk": ["how much of a medicine to take", "what someone is suffering from",
                         "which medicine to buy, stop or swap", "anything about a child",
                         "a password, family PIN or household code out loud"],
            "crisis": {
                "handling": ("Checked before anything else, before storage and before any plan answer. If "
                             "the caller or the person they care for sounds in danger, the assistant reads "
                             "the emergency redirect, saves nothing and does not continue the plan. No "
                             "intent is allowed to go past that redirect."),
                "prompt": "CrisisPrompt",
                "spokenWords": docs["crisisRedirect"],
                "storesAnything": False,
            },
            "childSafety": {"handling": ("CareLoop keeps adult care plans. A note about a child is refused "
                                         "with a plain explanation and nothing is stored."),
                            "storesAnything": False},
            "promptInjection": {"handling": ("Text that reads like instructions for a system rather than "
                                             "care for a person is refused on the voice surface."),
                                "redirect": "the companion page"},
            "unlinked": {"handling": "Anything about a real plan needs the household linked first.",
                         "prompt": "LinkHouseholdPrompt", "spokenWords": docs["guestNotice"]},
            "ambiguousPerson": {"handling": ("When more than one person could be meant, ask who and then "
                                             "continue; the server returns a short question to speak."),
                                "prompt": "ClarifyPersonPrompt"},
            "roleDenied": {"handling": "A household role that can't act by voice says so and points at the "
                                       "companion page.", "prompt": "NotAllowedPrompt"},
            "unknownItem": {"handling": "An item that is no longer on screen is not guessed at; the plan is "
                                        "asked for again.", "prompt": "NoPlanPrompt"},
            "speakerScope": {"handling": ("What may be read out depends on who the household is answering. "
                                          "The linked household stores which member hears whose plan and "
                                          "whether an instruction still waiting on the clinician may be "
                                          "spoken to them; a question outside that boundary is refused with "
                                          "one plain sentence that the service itself returns, and the "
                                          "refusal is recorded in that household's own ledger. A household "
                                          "that declares no boundary is answered exactly as before."),
                             "decidedBy": "the linked household's stored boundary",
                             "deviceDecides": False,
                             "refusalStoresAnything": False},
        },
        "intentsNotOnThisSurface": [{"tool": t, "reason": r} for t, r in docs["withheld"].items()],
        "welcomePrompt": "WelcomePrompt",
        "helpPrompt": "HelpPrompt",
        "fallbackPrompt": "FallbackPrompt",
        "stopHandling": {"note": ("Cancel and stop are supplied by the platform, so they are not restated "
                                  "here: the developer's copy stays free of platform brand terms.")},
        "notes": [
            "One intent per voice-allowed tool, plus device-local intents that call nothing at all. A tool "
            "name appears only inside routing and is never spoken.",
            "Each intent declares its slots (name, type, required) for dialog delegation, and "
            "routing.slotArguments maps a collected slot onto the server's own argument name. A slot with "
            "no mapping is collected for speaking and confirmation only and is never sent.",
            BRAND_NOTE,
        ],
    }
    return model


def sample_utterances():
    return {
        "WhatsMyPlanIntent": [
            "what is next", "what is next for {Person}", "what is the plan for {Person}",
            "what did the clinician say for {Person}", "what should {Person} do now",
            "what has {Person} still got to do", "list the {Section} items for {Person}",
            "what was I told to do for {Person}", "show me the plan for {Person}",
            "what is still pending for {Person}",
        ],
        "TodaysPlanIntent": [
            "what is on today", "what is the plan for today", "what is on today for {Person}",
            "what does {Person} have today", "today's day for {Person}", "what happens this morning",
            "what is there for {Person} tonight", "walk me through today for {Person}",
        ],
        "MarkDoneIntent": [
            "mark the {ItemPosition} one as done", "the {ItemPosition} one is done",
            "{Person} finished the {ItemPosition} item", "tick off the {ItemPosition} thing",
            "record the {ItemPosition} item as finished", "that one is sorted",
            "the {ItemPosition} thing has been dealt with for {Person}",
        ],
        "ExplainInstructionIntent": [
            "why did the clinician say that", "why is the {ItemPosition} item there",
            "explain the {ItemPosition} one", "what were the exact words for the {ItemPosition} item",
            "when was that recorded", "where did that come from", "read back the {ItemPosition} instruction",
        ],
        "AddVerbalNoteIntent": [
            "remember that {NoteText}", "make a note that {NoteText}", "add a note for {Person}",
            "{Person} was told {NoteText}", "keep this with the plan", "write down {NoteText}",
        ],
        "ListPeopleIntent": [
            "who is in my household", "who do you have plans for", "list the people in the plan",
            "whose plan are you looking after", "who can I ask about",
        ],
        "WhatChangedIntent": [
            "what changed since the last visit", "what is new since the last appointment",
            "what changed for {Person}", "did anything stop since last time",
            "compare this with the previous visit for {Person}",
        ],
        "DailyDigestIntent": [
            "how am I doing today", "how much of the plan is done", "what is still left today",
            "what has {Person} got left today", "am I on track for {Person}",
        ],
        "PrepareVisitQuestionsIntent": [
            "what should I ask at the next visit", "prepare questions for the next appointment",
            "what do I need to check with the clinician for {Person}", "what is still flagged for {Person}",
            "make me a list for the next visit",
        ],
        "RemoveInstructionIntent": [
            "delete the {ItemPosition} item", "remove the {ItemPosition} one from the plan",
            "take the {ItemPosition} instruction away for {Person}", "cross out the {ItemPosition} thing",
            "that instruction is wrong, remove it",
        ],
        "AboutCareLoopIntent": [
            "what can you do", "what are you for", "how does this work", "what can CareLoop help with",
            "what do you know about",
        ],
        "AdjustDeliveryIntent": [
            "say that {Delivery}", "say it {Delivery}", "speak {Delivery}", "read it back {Delivery}",
            "can you say that {Delivery}", "could you be {Delivery}",
        ],
        "IdentifySpeakerIntent": [
            "this is {Person}", "I am {Person}", "speaking as {Person}", "answer as {Person}",
            "switch to {Person}", "you are talking to {Person}",
        ],
    }


# ==================================================================== store listing
FULL_DESCRIPTION = """CareLoop keeps the care instructions your clinician already gave in one place the whole household can ask about.

A discharge conversation is long, and the paper it ends up on goes in a drawer. CareLoop stores the words the clinician actually used, for each person a household looks after, with the date they were recorded, and lets you ask about them out loud when your hands are full.

ASK IT OUT LOUD
  - What is next for someone, in the order the plan means it.
  - Why an item is there, and the exact recorded words behind it.
  - What today looks like, sorted by morning, after food, evening and bedtime.
  - What you have already marked done and what is still pending.
  - What changed compared with the previous visit: new, stopped, changed.
  - Which questions to take to the next appointment.
  - Something you were told verbally and nobody wrote down. CareLoop remembers it in your words.

WHAT IT NEVER DOES
It is not medical advice and does not pretend to be. CareLoop never gives advice, never changes an instruction, never adds one of its own and never guesses. If it has no recording about something, it says so. Anything unclear is left flagged for the clinician rather than smoothed over, and it tells you it is unclear.

AMOUNTS ARE NOT SPOKEN
On the voice surface every medicine amount is replaced with 'your prescribed amount'. Amounts are never spoken and never shown on the voice card. They live only on the CareLoop companion page, which opens for your household after signing in. The masking is not a preference you can lose: it is applied to every field the assistant can return and checked once more on the way out.

YOUR HOUSEHOLD'S DATA
Each household has its own encrypted store, separate from every other. The original words you record stay inside it. Nothing is sold, matched or shared. You can erase everything from the companion page, and the receipt is only given once the store and its key are genuinely gone. Unlinking your assistant stops it reading the plan straight away.

If anyone sounds in danger, say so: CareLoop points at your local emergency number and saves nothing.

CareLoop is self hosted. A household runs its own copy, and this add-on connects your assistant to that copy. Not a medical device."""

SHORT_DESCRIPTION = ("Repeats the care plan your clinician recorded, for the household to ask out loud. "
                     "No amounts by voice.")
EXAMPLE_PHRASES = [
    "Ask CareLoop what is next for my father today.",
    "Ask CareLoop why the clinician said the second item, and what the exact words were.",
    "Ask CareLoop to mark the first item as done.",
    "Ask CareLoop what changed since the last visit and what to take to the next appointment.",
]


def build_store_listing(docs, digests):
    public = docs["publicUrl"]
    placeholder = is_placeholder_url(public)

    def icon_entry(size):
        name = "icon-%dx%d.png" % (size, size)
        return {"size": "%dx%d" % (size, size),
                "description": "CareLoop loop mark on a warm paper background",
                "localPath": "%s/%s" % (ASSET_DIR, name),
                "sha256": digests[name]}

    def carousel_entry():
        name = "carousel-%dx%d.png" % (CAROUSEL_W, CAROUSEL_H)
        return {"size": "%dx%d" % (CAROUSEL_W, CAROUSEL_H),
                "description": ("One person's day on screen: the loop mark, four plan items in the "
                                "clinician's own words, two already marked done."),
                "localPath": "%s/%s" % (ASSET_DIR, name),
                "sha256": digests[name]}

    locales = {}
    for code in LOCALES:
        locales[code] = {
            "name": {"value": "CareLoop"},
            "summary": {"value": "The care plan your clinician wrote down, asked out loud."},
            "shortDescription": SHORT_DESCRIPTION,
            "fullDescription": FULL_DESCRIPTION,
            "examplePhrases": list(EXAMPLE_PHRASES),
            "descriptionKeywords": ["care plan", "caregiver", "clinician instructions", "household",
                                    "older adults", "follow up", "discharge"],
            "mediaAssets": {"icons": {"light": [icon_entry(s) for s in ICON_SIZES]},
                            "carouselImages": [carousel_entry()]},
            "privacyAndCompliance": {
                "privacyPolicyUrl": public + "/privacy",
                "termsOfUseUrl": public + "/terms",
                "controlsRequested": [
                    {"name": "ACCOUNT_LINKING", "description": {"value": "Link or unlink this household"}},
                    {"name": "ACCOUNT_LINKING_STATUS", "description": {"value": "See whether the household is linked"}},
                    {"name": "DELETE_USER_DATA", "description": {"value": "Erase the household's whole plan"}},
                ],
            },
            "testingInstructions": (
                "Point the add-on at your own deployment and link the household with the household code "
                "and family PIN from that deployment's own environment - never say them on a shared "
                "device. Then ask what is next for the person in the plan, ask why an item was recorded, "
                "mark one item done and ask what changed. Every spoken answer must show any amount as "
                "'" + MASK_TOKEN + "' and must name the clinician and the recording date. Ask the same "
                "thing signed out: the answer should be the link notice, not a plan."),
        }

    listing = {
        "schemaVersion": "careloop-store-listing/1",
        "generatedBy": "scripts/make_addon.py",
        "storeListing": {"skillType": "custom", "distributorCategory": "HealthAndWellness",
                         "publishAsBeta": False, "isTemplate": False, "locales": locales},
        "category": "HealthAndWellness",
        "subcategory": "Caregiving",
        "locales": list(LOCALES),
        "defaultLocale": LOCALES[0],
        "originLocales": [LOCALES[0]],
        "distribution": {"regions": ["IN", "US", "GB"], "pricing": {"type": "FREE"},
                         "isAvailableForKids": False, "isAvailableForWorksAndEducation": False},
        "developer": {"organisation": "CareLoop", "supportUrl": public + "/docs",
                      "contactEmailEnv": "CARELOOP_SUPPORT_EMAIL",
                      "note": "The contact address is held in the environment, never in this file."},
        "serviceEndpoint": {"mcpUri": docs["endpoint"],
                           "note": "Self hosted: the household's own deployment, discovered through the "
                                   "OAuth metadata published by that deployment."},
        "voiceSurfaceGuarantees": {
            "noMedicineAmountsSpoken": True,
            "amountsAreShownOnlyOn": "the household companion page, after signing in",
            "noMedicalAdvice": docs["disclaimer"],
            "storesOnlyWhatAClinicianSaid": True,
            "noChildrensData": True,
            "crisisHandling": "a spoken redirect to local emergency help, and nothing saved",
        },
        "mediaAssetNote": ("The icons and the carousel are generated into the assets folder beside this "
                           "file by scripts/make_addon.py and are referenced by local path. Host them at "
                           "the deployment's public HTTPS origin and put those absolute addresses here "
                           "before submission."),
        "publicationReady": not placeholder,
        "mustReplaceBeforePublication": ([] if not placeholder else
                                        ["storeListing.locales.*.privacyAndCompliance.privacyPolicyUrl",
                                         "storeListing.locales.*.privacyAndCompliance.termsOfUseUrl",
                                         "developer.supportUrl",
                                         "serviceEndpoint.mcpUri",
                                         "the icon and carousel local asset paths (host them over HTTPS)"]),
        "brandTermNote": BRAND_NOTE,
    }
    return listing


def is_placeholder_url(url):
    parts = url.split("://")
    if len(parts) != 2 or parts[0] != "https":
        return True
    host = parts[1].split("/")[0].split(":")[0]
    if not host or DEV_URL_RX.search(url):
        return True
    if "." not in host or re.search(r"(example|invalid|localtest|test\d*$)", host, re.I):
        return True
    return False


# ============================================================ addon manifest builder
def build_addon(docs, extra_notes, warnings):
    public = docs["publicUrl"]
    endpoint = docs["endpoint"]
    prm, asmd, cfg = docs["prm"], docs["asMetadata"], docs["cfg"]
    caps = docs["capabilities"] or {}
    ext = caps.get("extensions") or {}
    card = next((r for r in docs["resources"] or [] if r.get("uri") == docs["cardUri"]), {})
    # a device-local intent reaches no tool at all, so it is enumerated nowhere
    tool_intents = [i for i in ALL_INTENTS if "tool" in i]
    exposed = [i["tool"] for i in tool_intents]
    withheld = {t["name"]: docs["withheld"].get(t["name"], "not mapped to a voice intent")
                for t in docs["tools"] if t["name"] not in exposed}

    manifest = {
        "schemaVersion": "careloop-addon-manifest/1",
        "generatedBy": "scripts/make_addon.py",
        "addon": {
            "id": "careloop", "displayName": "CareLoop", "publisher": "CareLoop",
            "server": docs["serverInfo"],
            "description": ("Family care plan for older adults. The assistant repeats the instructions a "
                            "clinician already gave, as they were recorded. Medicine amounts are never "
                            "spoken on the voice surface."),
            "category": "caregiving",
            "privacyPolicyUrl": public + "/privacy", "termsOfUseUrl": public + "/terms",
        },
        "protocol": {
            "transport": "mcp-streamable-http",
            "endpoint": endpoint,
            "supportedProtocolVersions": list(docs["protocols"]),
            "preferredProtocolVersion": docs["negotiated"],
            "protocolVersionHeader": "MCP-Protocol-Version",
            "unknownProtocolVersionStatus": 400,
            "sessionHeader": "MCP-Session-Id",
            "sessionIssuedBy": "the initialize response",
            "acceptTypes": ["application/json"],
            "notificationsExpectedStatus": 202,
            "batchRequestsSupported": False,
            "serverInitiatedSseSupported": False,
            "getStatusOnEndpoint": 405,
            "maxRequestBodyBytes": cfg["maxBodyBytes"],
            "originValidationStatus": 403,
            "credentialTransport": "http-header-only; credentials in a query string are rejected",
            "healthChecks": [public + "/healthz", public + "/readyz"],
            "telemetry": {"metricsEndpoint": public + "/metrics", "p95TargetMs": 500},
        },
        "discovery": {
            "mode": docs["mode"],
            "observedFrom": docs["observedFrom"],
            "protocolProbeResults": docs["probeResults"],
            "readiness": docs["readiness"],
            "protectedResourceMetadata": prm,
            "authorizationServerMetadata": asmd,
            "initializeResult": {"protocolVersion": docs["negotiated"], "serverInfo": docs["serverInfo"],
                                 "capabilities": caps},
            "resourcesList": docs["resources"],
            "toolsListCount": len(docs["tools"]),
            "consistency": {
                "endpointIsPublicUrlPlusMcp": endpoint == public + "/mcp",
                "protectedResourceMatchesEndpoint": prm.get("resource") == endpoint,
                "authorizationServersContainPublic": public in (prm.get("authorization_servers") or []),
                "issuerMatchesPublicUrl": asmd.get("issuer") == public,
                "tokenEndpointMatchesPublicUrl": asmd.get("token_endpoint") == public + "/oauth/token",
                "scopesAgree": prm.get("scopes_supported") == asmd.get("scopes_supported"),
                "negotiatedVersionIsSupported": docs["negotiated"] in docs["protocols"],
            },
        },
        "authentication": {
            "type": "oauth2.1-authorization-code-pkce",
            "registrationModel": "static-client-credentials",
            "note": ("The server ships a fixed client credential pair per environment (careloop/oauth.py): "
                     "no dynamic registration endpoint exists, so the add-on is configured with the client "
                     "id below and the secret is held by the platform. Only the environment variable name "
                     "is recorded here, never a secret value."),
            "client": {
                "clientId": cfg["alexaClientId"], "clientIdEnv": "ALEXA_CLIENT_ID",
                "clientSecretEnv": "ALEXA_CLIENT_SECRET", "clientSecretStoredHere": False,
                "tokenEndpointAuthMethod": "client_secret_basic",
                "redirectUris": cfg["redirectUris"], "redirectMatch": "exact",
                "secondClient": {"purpose": "the companion web page", "clientIdEnv": "CARELOOP_CLIENT_ID",
                                 "clientSecretEnv": "CARELOOP_CLIENT_SECRET"},
            },
            "flows": {
                "authorizationCodeWithPkce": {
                    "authorizationEndpoint": asmd.get("authorization_endpoint"),
                    "tokenEndpoint": asmd.get("token_endpoint"),
                    "callbackUrl": public + "/callback",
                    "responseTypes": asmd.get("response_types_supported"),
                    "codeChallengeMethods": asmd.get("code_challenge_methods_supported"),
                    "tokenEndpointAuthMethods": asmd.get("token_endpoint_auth_methods_supported"),
                    "scopes": ["mcp:tools"],
                    "resourceIndicator": {"sent": True, "mustEqual": endpoint,
                                          "note": ("RFC 8707 resource is checked against the server's own "
                                                   "public URL; a mismatch is an authorization error.")},
                    "accessTokenTtlSeconds": cfg["accessTtl"],
                    "refreshTokenTtlSeconds": cfg["refreshTtl"],
                    "refreshTokenRotation": True,
                    "refreshRequiresResource": False,
                    "householdConsentScreen": True,
                    "consentFactors": ["household code", "family PIN", "one-time consent checkbox"],
                    "consentText": docs["consentText"],
                },
                "clientCredentials": {
                    "tokenEndpoint": asmd.get("token_endpoint"),
                    "scope": "mcp:service",
                    "accessTokenTtlSeconds": cfg["serviceTtl"],
                    "refreshTokenIssued": False,
                    "canDo": ["initialize", "about_careloop"],
                    "note": ("A service token may open a session and answer the guest-safe capability "
                             "question only. Every other tool answers 401 with a WWW-Authenticate header "
                             "pointing at the protected-resource metadata, which is what starts linking."),
                },
            },
            "scopes": {"mcp:tools": "household-linked caregiver identity, needed for any plan answer",
                       "mcp:service": "unsigned service identity, guest-safe surface only"},
            "tokenBinding": {"audience": endpoint,
                            "unlinkBehaviour": "revoked links stop refresh tokens immediately"},
            "discoveryUrls": {
                "oauthProtectedResource": public + "/.well-known/oauth-protected-resource",
                "oauthAuthorizationServer": public + "/.well-known/oauth-authorization-server",
                "openIdConfiguration": public + "/.well-known/openid-configuration"},
        },
        "capabilities": {
            "tools": caps.get("tools", {}),
            "resources": caps.get("resources", {}),
            "extensions": {"mcp-apps": ext.get("mcp-apps")},
            "mcpAppCard": {
                "uri": docs["cardUri"], "name": card.get("name"), "mimeType": card.get("mimeType"),
                "description": card.get("description"), "protocolExtension": ext.get("mcp-apps"),
                "readMethod": "resources/read", "toolsThatRenderIt": docs["uiTools"],
                "resultMeta": {"_meta.ui.resourceUri": docs["cardUri"]},
                "surfaceRules": ("The card is the large-text view of the same dose-free answer. Amounts "
                                 "appear only on the household companion page, never in the card."),
            },
            "elicitation": {
                "supported": True, "shape": {"needs_clarification": "boolean", "question": "string"},
                "note": ("Ambiguity comes back as a short question so the voice surface can ask 'Who is "
                         "this about?' and continue."),
            },
        },
        "toolPolicy": {
            "surface": "voice",
            "toolContractHash": tool_contract_hash(docs["tools"]),
            "toolsDeclared": len(docs["tools"]),
            "voiceExposed": exposed,
            "voiceWithheld": withheld,
            "guestSafe": docs["guestSafe"],
            "cardTools": docs["uiTools"],
            "intentMap": {i["tool"]: i["intent"] for i in tool_intents},
            "authorizationActions": {k: v for k, v in docs["actions"].items() if v},
            "annotations": docs["annotations"],
            "destructive": {
                "tool": "delete_instruction",
                "userConfirmationRequired": True,
                "twoStep": ("the first call only reads the item back with a confirmation question; the "
                            "removal happens on the second call after the customer confirms"),
            },
            "outputGuarantees": {
                "structuredContentOnly": True,
                "spokenTextBuiltByTheServer": True,
                "doseMaskingOnEveryStringField": True,
                "maskToken": MASK_TOKEN,
                "disclaimerOnEveryAnswer": True,
                "attributionOnEveryCareAnswer": True,
                "leakCheck": ("the response is scanned for a residual amount before it is emitted; a hit "
                              "is re-masked and counted as an incident"),
                "nothingInvented": ("extraction is extractive: every character is either a verbatim "
                                    "substring of what was recorded or a constant from a closed vocabulary"),
            },
            "refusalRules": {
                "childContent": "adult care plans only - a note naming a child is refused",
                "crisisContent": "the emergency redirect is spoken and nothing is stored",
                "promptInjection": "text that reads like instructions for a system is refused",
            },
            "neverExpose": ["any medicine amount", "the raw visit transcript", "another household's data",
                            "the export route", "suggestions about medicines"],
        },
        "notes": extra_notes + [
            "Field names here follow this repo's server contract. Match them to the live developer console "
            "schema before publication - see alexa/README.md."],
        "blockers": [],
    }
    manifest["blockers"] = blockers_for(public, cfg, docs)
    if warnings:
        manifest["warnings"] = list(warnings)
    return manifest


def blockers_for(public, cfg, docs):
    out = []
    env = env_defaults()
    if is_placeholder_url(public):
        out.append("CARELOOP_PUBLIC_URL is still a development URL (%s); the add-on needs a public HTTPS "
                   "origin before submission." % public)
        out.append("privacyPolicyUrl and termsOfUseUrl point at that same development origin and must be "
                   "replaced with the published pages.")
    if cfg["alexaClientId"] == env.get("ALEXA_CLIENT_ID") or "placeholder" in cfg["alexaClientId"]:
        out.append("ALEXA_CLIENT_ID is still the value shipped in .env.example; register a real client "
                   "credential pair and rotate it.")
    if (docs.get("readiness") or {}).get("auth") == "off":
        out.append("This deployment answers the MCP endpoint with authentication off, so initialize and "
                   "tools/list worked without a token. Re-run against a deployment in OAuth mode before "
                   "submitting: the linking flow this manifest describes cannot be exercised otherwise.")
    if docs["mode"] == "offline":
        out.append("Protocol versions were read from careloop.mcp_server.PROTOCOLS. Re-run with "
                   "--from-server against the deployed URL to confirm what the live server negotiates.")
    out.append("Secrets are never written into these files; the values shipped in .env.example are "
               "placeholders and must be rotated before submission.")
    return out


def env_defaults():
    """Only the placeholder NAMES/VALUES from .env.example, so we can tell if a real one is unset."""
    out = {}
    try:
        with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


# ============================================================== compliance scanning
def all_strings(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from all_strings(v, "%s[%d]" % (path, i))
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from all_strings(v, "%s.%s" % (path, k))


LOCATOR_KEY_RX = re.compile(r"(path|url|uri|endpoint|dir|file|hash|digest)$", re.I)


def is_locator(key, value):
    """A filesystem path or a URL, i.e. plumbing rather than the developer's own copy.

    The brand-term ban covers customer-facing wording. This deliverable's directory is
    named after the platform by the task spec, so its asset paths stay as they are.
    """
    if LOCATOR_KEY_RX.search(key or ""):
        return True
    return " " not in value and "/" in value


def copy_findings(doc, *, declared_origin=None, spoken_keys=None, tool_names=()):
    """Checks the validator does not make: brand terms in developer copy, a residual amount,
    a development or placeholder URL passed off as final, and internal names in spoken copy."""
    pg = policy_guard()
    problems = []
    # the slash-namespaced MCP _meta keys are field names a machine sends, not copy a device speaks
    machine_keys = {pg.SPEAKER_META_KEY, pg.VOICE_PROFILE_META_KEY, pg.SIMULATED_PROFILE_VALUE}
    for path, text in all_strings(doc):
        key = path.rsplit(".", 1)[-1] if "." in path else path
        if text in machine_keys:
            continue
        locator = is_locator(key, text)
        if not locator and BRAND_RX.search(text):
            problems.append("%s: protected brand term in the developer's copy (%s)" % (path, text[:56]))
        if pg.contains_dose(text):
            problems.append("%s: carries an amount the voice surface may not carry (%s)"
                            % (path, text[:56]))
        if DEV_URL_RX.search(text) and not (declared_origin and declared_origin in text):
            problems.append("%s: development URL not marked as a placeholder (%s)" % (path, text[:56]))
        if not locator and ("your-host" in text or "change-me" in text):
            problems.append("%s: placeholder value left in copy (%s)" % (path, text[:56]))
        if spoken_keys is not None and key in spoken_keys:
            low = text.lower()
            for tname in tool_names:
                if tname in low:
                    problems.append("%s: spoken copy names the tool '%s' (%s)" % (path, tname, text[:56]))
            if NUMERIC_ID_RX.search(text):
                problems.append("%s: spoken copy carries an identifier (%s)" % (path, text[:56]))
            for word in ("json", "structuredcontent", "payload"):
                if word in low:
                    problems.append("%s: technical word in spoken copy (%s)" % (path, word))
    return problems


# ====================================================================== output assembly
def dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def build_all(public_url, from_server=None):
    warnings, notes = [], []
    if from_server:
        docs = live_docs(from_server)
        if docs["publicUrl"] != public_url:
            notes.append("The live server publishes its own canonical origin (%s) and the manifest follows "
                         "it, because the token audience and the OAuth resource are bound to that string - "
                         "not to the address the documents were fetched from (%s)."
                         % (docs["publicUrl"], from_server))
            say("live server at %s publishes %s; using the live values over the local %s"
                % (from_server, docs["publicUrl"], public_url))
            warnings.append("generated from the live documents at %s" % from_server)
        public_url = docs["publicUrl"]
    else:
        docs = careloop_docs(public_url)

    pg = policy_guard()
    import careloop.oauth as oauth_mod
    import careloop.tools as cl_tools
    # Device-local intents are deliberately left out: they call nothing, so they neither use nor withhold
    # a tool, and a withheld tool is one the caregiver can only reach on a screen.
    voice_tools = {i["tool"] for i in ALL_INTENTS if "tool" in i}
    docs["withheld"] = {t: ("no voice intent maps onto it: it is only meaningful where the caregiver can "
                            "see the whole record on the household companion page")
                        for t in sorted({d["name"] for d in cl_tools.TOOL_DEFS} - voice_tools)}
    docs["disclaimer"] = pg.DISCLAIMER
    docs["crisisRedirect"] = pg.CRISIS_REDIRECT
    docs["guestNotice"] = pg.GUEST_NOTICE
    docs["consentText"] = oauth_mod.CONSENT_TEXT

    pal, missing = read_palette()
    if missing:
        say("tokens.css did not define %s; built-in fallback colours were used" % ", ".join(missing))
    assets = build_assets(pal)
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}

    listing = build_store_listing(docs, digests)
    model = build_interaction_model(docs)
    addon = build_addon(docs, notes, warnings)

    files = {"alexa/addon.json": dumps(addon).encode(),
             "alexa/store-listing.json": dumps(listing).encode(),
             "alexa/interaction-model.json": dumps(model).encode()}
    for name, data in sorted(assets.items()):
        files["%s/%s" % (ASSET_DIR, name)] = data
    return files, {"listing": listing, "model": model, "addon": addon, "docs": docs, "assets": assets,
                   "digests": digests, "public": docs["publicUrl"],
                   "validator": pg.validate_store_listing(listing)}


def policy_guard():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from careloop import policy_guard
    return policy_guard


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")


def routing_findings(model, docs):
    """Cross-check the voice routing against the tool contracts the same run writes.

    The interaction model and `artifacts/contracts/*.schema.json` come out of this script through
    different code paths, so nothing else notices when one of them moves. Each case below is a live
    failure on a real device, not a style point: an argument the tool does not accept, a required
    argument no collected slot can fill, a family's synonym the tool's enum would reject, a slot the
    dialog would elicit that no sample can ever speak, or a confirmation prompt that names a person
    the intent never collected.
    """
    problems = []
    schemas = {t["name"]: t.get("inputSchema", {}) for t in docs["tools"]}
    types = {t["name"]: t for t in model["languageModel"]["types"]}
    prompts = {p["id"]: " ".join(p["body"]) for p in model["promptCatalog"]["prompts"]}
    for intent in model["languageModel"]["intents"]:
        name = intent["name"]
        routing = intent["routing"]
        tool = routing["tool"]
        # A device-local intent calls nothing at all: it changes how an answer is spoken, or who the
        # device is answering for. Every comparison below against a tool contract is vacuous for it, and
        # the four checks here are what keep it that way — an intent that stops being unable to write
        # stops being device-local, and this is where the generator notices.
        if routing.get("type") == "device-local":
            schema, props = {}, {}
            if routing.get("slotArguments"):
                problems.append("%s is device-local but still maps a slot onto an argument" % name)
            if routing.get("confirmArgument"):
                problems.append("%s is device-local but still holds a yes open" % name)
            if routing.get("writesAnything") is not False:
                problems.append("%s is device-local and must declare in words that it writes nothing"
                                % name)
            if name in model["dialog"]["confirmationRequired"]:
                problems.append("%s is device-local, so the dialog may not hold it for a yes" % name)
        elif tool not in schemas:
            problems.append("%s routes to '%s', which the server does not expose" % (name, tool))
            continue
        else:
            schema = schemas[tool]
            props = schema.get("properties", {})
        declared = {s["name"]: s for s in intent.get("slots", [])}
        for slot_name, slot in sorted(declared.items()):
            slot_type = types.get(slot["type"])
            if slot_type is None:
                problems.append("%s: slot %s names type %s, which the model does not declare"
                                % (name, slot_name, slot["type"]))
            elif bool(slot.get("wildcard")) != bool(slot_type.get("wildcard")):
                problems.append("%s: slot %s and its type %s disagree about being a wildcard"
                                % (name, slot_name, slot["type"]))
            prompt = slot.get("prompt")
            if prompt is not None and prompt not in prompts:
                problems.append("%s: slot %s asks with %s, which the prompt catalog does not define"
                                % (name, slot_name, prompt))
            if slot.get("required") and not prompt:
                problems.append("%s: slot %s is required but nothing declares how to ask for it"
                                % (name, slot_name))

        mapped = routing.get("slotArguments", {})
        for slot_name, arg in sorted(mapped.items()):
            if slot_name not in declared:
                problems.append("%s: routing maps %s, which the intent never declares as a slot"
                                % (name, slot_name))
                continue
            if arg not in props:
                problems.append("%s: routing sends %s as '%s', which %s does not accept (%s)"
                                % (name, slot_name, arg, tool,
                                   ", ".join(sorted(props)) or "no arguments at all"))
                continue
            enum = props[arg].get("enum")
            slot_type = types.get(declared[slot_name]["type"])
            if enum and slot_type and not slot_type.get("wildcard"):
                bad = sorted({v["id"] for v in slot_type["values"]} - set(enum))
                if bad:
                    problems.append("%s: %s could send '%s' as %s, which is not one of the kinds %s "
                                    "accepts (%s)" % (name, slot_name, "/".join(bad), arg, tool,
                                                      ", ".join(enum)))
        confirm_arg = routing.get("confirmArgument")
        if confirm_arg is not None and confirm_arg not in props:
            problems.append("%s: the yes/no answer lands on '%s', which %s does not accept"
                            % (name, confirm_arg, tool))

        reachable = {arg for slot_name, arg in mapped.items() if declared.get(slot_name, {}).get("required")}
        if confirm_arg:
            reachable.add(confirm_arg)
        for required_arg in sorted(schema.get("required", [])):
            if required_arg not in reachable:
                problems.append("%s: %s requires '%s', but no required slot maps to it, so the call could "
                                "always fail" % (name, tool, required_arg))

        spoken = set()
        for sample in intent["samples"]:
            spoken |= set(PLACEHOLDER_RE.findall(sample))
        for undeclared in sorted(spoken - set(declared)):
            problems.append("%s: a sample speaks {%s} but the intent never declares that slot"
                            % (name, undeclared))
        for unfilled in sorted(set(declared) - spoken):
            problems.append("%s: slot %s is declared and would be elicited, but no sample can supply it"
                            % (name, unfilled))

        rule = model["dialog"]["confirmationRequired"].get(name)
        if rule:
            body = prompts.get(rule["prompt"], "")
            # The confirmation is spoken before the call, so every value it names must be one the
            # dialog guarantees to have collected — a required slot, not a hoped-for optional one.
            must_have = {s["name"] for s in declared.values() if s.get("required")}
            for placeholder in sorted(set(PLACEHOLDER_RE.findall(body)) - must_have):
                problems.append("%s: its confirmation prompt speaks {%s}, which the dialog does not "
                                "guarantee has been collected" % (name, placeholder))

    named = {i["name"] for i in model["languageModel"]["intents"]}
    for section in ("confirmationRequired", "confirmationResponses"):
        for intent_name in sorted(model["dialog"][section]):
            if intent_name not in named:
                problems.append("dialog.%s names %s, which the model has no intent for"
                                % (section, intent_name))
    return problems


def proactive_findings(model, docs):
    """The device-initiated reminder path, checked against the same live documents.

    A turn the customer never asked for is the one place this skill could speak something nobody
    requested, so every part of it is pinned to the artifact: the intent that arms it must exist,
    must route to a tool the server itself marks read-only, must not be one the dialog holds open
    for a yes (a connection directive cannot share a response with a dialog directive), and the
    prompt it opens with must be in the catalog."""
    problems = []
    reminder = model.get("proactive", {}).get("reminder")
    if not reminder:
        return ["proactive: no reminder is declared"]
    intents = {i["name"]: i for i in model["languageModel"]["intents"]}
    prompts = {p["id"] for p in model["promptCatalog"]["prompts"]}
    name = reminder["intent"]
    intent = intents.get(name)
    if intent is None:
        problems.append("proactive: the reminder arms on %s, which the model has no intent for" % name)
    else:
        tool = intent["routing"]["tool"]
        schema = {t["name"]: t for t in docs["tools"]}.get(tool)
        if schema is None:
            problems.append("proactive: %s routes to %s, which the server does not expose" % (name, tool))
        elif not schema.get("annotations", {}).get("readOnlyHint"):
            problems.append("proactive: a device-initiated turn would call %s, which is not read-only"
                            % tool)
        if name in model["dialog"]["confirmationRequired"]:
            problems.append("proactive: %s needs a yes before it can be called, so its answer cannot "
                            "also carry a connection directive" % name)
    if reminder["prompt"] not in prompts:
        problems.append("proactive: the reminder opens with %s, which the prompt catalog does not define"
                        % reminder["prompt"])
    if not reminder["resumeAfterSeconds"] > 0:
        problems.append("proactive: resumeAfterSeconds must be a positive number of seconds")
    for key, want in (("requestType", "SessionResumedRequest"), ("causeType", "ConnectionCompleted"),
                      ("directiveType", "Connections.StartConnection"),
                      ("onCompletion", "RESUME_SESSION")):
        if reminder[key] != want:
            problems.append("proactive: %s is %r, and the device protocol names it %r"
                            % (key, reminder[key], want))
    if not reminder["connectionUri"].startswith("connection://"):
        problems.append("proactive: connectionUri %r is not the connection://name/version form"
                        % reminder["connectionUri"])
    return problems


def presentation_findings(model):
    """The spoken-delivery path, pinned to the artifact exactly as the reminder is.

    A rate or a word ceiling that only lives inside a device is a number nobody can audit, and a style
    allowed to speak more than the model's own ceiling would be the voice budget loosened because a
    customer asked to be understood better. So the intent must exist and must be device-local, every style
    id must be a value the slot type really offers, every line must be a catalog prompt, every rate and
    volume must be a percentage the speech format understands, and no style may speak more words than
    `answerGuidance` already allows."""
    problems = []
    delivery = model.get("presentation", {}).get("delivery")
    if not delivery:
        return ["presentation: no delivery style is declared"]
    intents = {i["name"]: i for i in model["languageModel"]["intents"]}
    prompts = {p["id"] for p in model["promptCatalog"]["prompts"]}
    types = {t["name"]: t for t in model["languageModel"]["types"]}
    intent = intents.get(delivery["intent"])
    if intent is None:
        return ["presentation: %s is not an intent the model declares" % delivery["intent"]]
    if intent["routing"].get("type") != "device-local":
        problems.append("presentation: %s must be device-local — a style that could call a tool is an "
                        "accessibility setting that can change a plan" % delivery["intent"])
    slot = next((s for s in intent.get("slots", []) if s["name"] == delivery["slot"]), None)
    style_ids = set()
    if slot is None:
        problems.append("presentation: %s collects %s, which its intent never declares"
                        % (delivery["intent"], delivery["slot"]))
    else:
        style_ids = {v["id"] for v in types.get(slot["type"], {}).get("values", [])}
    ceiling = model["answerGuidance"]["maxSpokenWords"]
    for style, spec in sorted(delivery["styles"].items()):
        if style not in style_ids:
            problems.append("presentation: style %s is not a value the %s slot offers (%s)"
                            % (style, delivery["slot"], ", ".join(sorted(style_ids)) or "none"))
        if spec["prompt"] not in prompts:
            problems.append("presentation: %s opens with %s, which the prompt catalog does not define"
                            % (style, spec["prompt"]))
        for key in ("rate", "volume"):
            value = spec.get(key)
            if value is None:
                continue
            if not PERCENT_RX.fullmatch(value):
                problems.append("presentation: %s declares %s %r, which is not a percentage the speech "
                                "format accepts" % (style, key, value))
            elif not 10 <= int(value.rstrip("%")) <= 300:
                problems.append("presentation: %s declares %s %s, outside the 10%%-300%% range a listener "
                                "can still make out" % (style, key, value))
        words = spec["maxSpokenWords"]
        if not isinstance(words, int) or words < 1:
            problems.append("presentation: %s declares a word ceiling of %r" % (style, words))
        elif words > ceiling:
            problems.append("presentation: %s would speak up to %d words and the model's own ceiling is %d"
                            % (style, words, ceiling))
    if delivery["defaultStyle"] not in delivery["styles"]:
        problems.append("presentation: the default style %s is not one of the declared styles"
                        % delivery["defaultStyle"])
    if delivery.get("writesAnything") is not False:
        problems.append("presentation: changing how an answer is spoken must declare that it writes nothing")
    if not delivery.get("appliesToHeldAnswer"):
        problems.append("presentation: a delivery request means saying the answer already in hand again, "
                        "differently — not fetching something new")
    return problems


def voice_profile_findings(model):
    """Whose voice the device answers for, and who decides what that person may hear.

    The device asserts and the service decides, so the declaration is checked for the things that would
    make it a lie: a field name that is not namespaced as ours (MCP reserves a slash-namespaced `_meta`
    key for exactly this), a profile that does not admit it is simulated, an intent that could reach a
    tool, and any claim that the device settled the boundary itself."""
    problems = []
    assertion = model.get("voiceProfile", {}).get("assertion")
    if not assertion:
        return ["voiceProfile: no speaker assertion is declared"]
    intents = {i["name"]: i for i in model["languageModel"]["intents"]}
    prompts = {p["id"]: " ".join(p["body"]) for p in model["promptCatalog"]["prompts"]}
    intent = intents.get(assertion["intent"])
    declared = set() if intent is None else {s["name"] for s in intent.get("slots", [])}
    if intent is None:
        problems.append("voiceProfile: %s is not an intent the model declares" % assertion["intent"])
    elif intent["routing"].get("type") != "device-local":
        problems.append("voiceProfile: saying who is speaking must never be able to call a tool")
    if assertion.get("slot") not in declared:
        problems.append("voiceProfile: the speaker is collected in slot %r, which %s does not declare"
                        % (assertion.get("slot"), assertion["intent"]))
    if assertion["prompt"] not in prompts:
        problems.append("voiceProfile: its line %s is not in the prompt catalog" % assertion["prompt"])
    else:
        # The line names the person back, so every value it speaks has to be a slot the intent collects.
        for placeholder in sorted(set(PLACEHOLDER_RE.findall(prompts[assertion["prompt"]])) - declared):
            problems.append("voiceProfile: %s speaks {%s}, which its intent never collects"
                            % (assertion["prompt"], placeholder))
    for key in ("speakerMetaKey", "profileMetaKey"):
        if not assertion[key].startswith("careloop/"):
            problems.append("voiceProfile: %s must be a namespaced _meta key under careloop/ (got %r)"
                            % (key, assertion[key]))
    if assertion["speakerMetaKey"] == assertion["profileMetaKey"]:
        problems.append("voiceProfile: who is speaking and how they were identified cannot share one key")
    if not assertion["simulated"]:
        problems.append("voiceProfile: a browser device and a script cannot hear a voice, so the assertion "
                        "must be labelled simulated")
    if assertion["deviceDecidesScope"] is not False:
        problems.append("voiceProfile: the device does not decide the boundary — the linked household's "
                        "stored scope does")
    if assertion["writesAnything"] is not False:
        problems.append("voiceProfile: naming a speaker writes nothing, and the declaration has to say so")
    guard = model["guardrails"].get("speakerScope")
    if not guard or guard.get("deviceDecides") is not False:
        problems.append("voiceProfile: guardrails.speakerScope must state that the device decides nothing")
    return problems


def findings(state):
    """Copy + validator + artwork problems, independent of what is on disk."""
    pg = policy_guard()
    problems = ["store listing: " + p for p in pg.validate_store_listing(state["listing"])]
    problems += ["store listing: " + p for p in copy_findings(
        state["listing"], declared_origin=state["public"])]
    problems += ["interaction model: " + p for p in copy_findings(
        state["model"], declared_origin=state["public"], spoken_keys=SPOKEN_KEYS,
        tool_names=sorted(state["docs"]["actions"]))]
    problems += ["interaction model: " + p for p in routing_findings(state["model"], state["docs"])]
    problems += ["interaction model: " + p for p in proactive_findings(state["model"], state["docs"])]
    problems += ["interaction model: " + p for p in presentation_findings(state["model"])]
    problems += ["interaction model: " + p for p in voice_profile_findings(state["model"])]
    # every declared pixel size must equal the real IHDR size
    declared_sizes = set()
    for loc in state["listing"]["storeListing"]["locales"].values():
        declared_sizes |= {i["size"] for i in loc["mediaAssets"]["icons"]["light"]}
        declared_sizes |= {c["size"] for c in loc["mediaAssets"]["carouselImages"]}
    for name, data in sorted(state["assets"].items()):
        w, h, depth, ctype = png_ihdr(data)
        got = "%dx%d" % (w, h)
        if got not in declared_sizes:
            problems.append("assets/%s: IHDR size %s is not declared in the listing" % (name, got))
        if depth != 8 or ctype != 6:
            problems.append("assets/%s: expected 8-bit RGBA (got depth %s, colour type %s)"
                            % (name, depth, ctype))
        if hashlib.sha256(data).hexdigest() != state["digests"][name]:
            problems.append("assets/%s: digest does not match the listing" % name)
    return problems


def drift_findings(files):
    problems = []
    for rel, data in sorted(files.items()):
        disk = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(disk):
            problems.append("missing committed file: %s" % rel)
            continue
        with open(disk, "rb") as f:
            cur = f.read()
        if cur != data:
            problems.append("drift: %s differs from a fresh generation (first differing byte %d of %d)"
                            % (rel, _first_diff(cur, data), max(len(cur), len(data))))
        elif rel.endswith(".json"):
            json.loads(cur.decode())
    return problems


def committed_copy_findings(state):
    """Also judge the files as committed, so a hand edit reports the compliance reason and
    not only the byte offset."""
    problems = []
    for rel, kind in (("alexa/store-listing.json", "listing"), ("alexa/interaction-model.json", "model")):
        disk = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(disk):
            continue
        try:
            with open(disk, encoding="utf-8") as f:
                doc = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append("%s: not readable JSON (%s)" % (rel, exc))
            continue
        if kind == "listing":
            problems += ["%s: " % rel + p for p in policy_guard().validate_store_listing(doc)]
            problems += ["%s: " % rel + p for p in copy_findings(doc, declared_origin=state["public"])]
        else:
            problems += ["%s: " % rel + p for p in copy_findings(
                doc, declared_origin=state["public"], spoken_keys=SPOKEN_KEYS,
                tool_names=sorted(state["docs"]["actions"]))]
            problems += ["%s: " % rel + p for p in routing_findings(doc, state["docs"])]
            problems += ["%s: " % rel + p for p in proactive_findings(doc, state["docs"])]
            problems += ["%s: " % rel + p for p in presentation_findings(doc)]
            problems += ["%s: " % rel + p for p in voice_profile_findings(doc)]
    return problems


def _first_diff(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return min(len(a), len(b))


# ------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate the Alexa+ add-on manifest, store listing, interaction model and "
                    "listing artwork for CareLoop.", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public-url", default=os.environ.get("CARELOOP_PUBLIC_URL") or DEFAULT_PUBLIC_URL,
                    help="canonical deployment origin (default $CARELOOP_PUBLIC_URL, then %s)"
                         % DEFAULT_PUBLIC_URL)
    ap.add_argument("--offline", action="store_true",
                    help="read the contract from the careloop package in process (the default)")
    ap.add_argument("--from-server", metavar="URL",
                    help="derive endpoint, protocol versions, scopes and tools from a running server")
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and exit non-zero on any drift or compliance problem")
    args = ap.parse_args(argv)
    if args.from_server and args.offline:
        ap.error("--from-server and --offline are mutually exclusive")

    public = args.public_url.rstrip("/")
    from_server = args.from_server
    if args.check:
        # --check is the CI gate, so it is always the hermetic offline generation: the committed
        # files are the offline ones, and a live fetch would add a provenance marker every run.
        from_server = None
        say("--check regenerates offline (careloop package in process); commit the offline output")

    files, state = build_all(public, from_server)
    problems = findings(state)
    if args.check:
        drift = drift_findings(files)
        problems += drift
        if drift:
            # explain the drift, not just report it
            problems += committed_copy_findings(state)
        if problems:
            print("make_addon --check FAILED (%d problem(s))" % len(problems))
            for p in problems:
                print("  - " + p)
            return 1
        print("make_addon --check OK")
        print("  committed files match a fresh generation: %d files" % len(files))
        print("  validate_store_listing -> %s" % (state["validator"] or "[]  (zero errors)"))
        print("  artwork verified against IHDR: %s" % ", ".join(
            "%s IHDR %dx%d" % (n, png_ihdr(d)[0], png_ihdr(d)[1])
            for n, d in sorted(state["assets"].items())))
        print("  publication origin %s  publicationReady=%s"
              % (state["public"], state["listing"]["publicationReady"]))
        return 0

    for rel, data in sorted(files.items()):
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print("wrote %-40s %8d bytes" % (rel, len(data)))

    errors = state["validator"]
    print()
    print("validate_store_listing(alexa/store-listing.json) -> %s"
          % (errors if errors else "[]  (zero errors: PASS)"))
    docs = state["docs"]
    print("mode                      : %s%s" % (docs["mode"],
                                                " (fetched from %s)" % docs["observedFrom"]
                                                if docs["observedFrom"] else ""))
    print("MCP endpoint              : %s" % docs["endpoint"])
    print("protocol versions         : %s   negotiated %s"
          % (", ".join(docs["protocols"]), docs["negotiated"]))
    print("scopes                    : %s" % ", ".join(docs["prm"]["scopes_supported"]))
    print("tools declared            : %d   reachable by voice: %d   withheld: %s"
          % (len(docs["tools"]), len([i for i in ALL_INTENTS if "tool" in i]),
             ", ".join(sorted(docs["withheld"])) or "none"))
    print("intents                   : %d   %d reach a tool, %d are device-local and reach none"
          % (len(ALL_INTENTS), len([i for i in ALL_INTENTS if "tool" in i]),
             len([i for i in ALL_INTENTS if i.get("deviceLocal")])))
    print("card resource             : %s (extension mcp-apps %s)"
          % (docs["cardUri"], (docs["capabilities"].get("extensions") or {}).get("mcp-apps")))
    print("artwork                   : %s" % ", ".join(
        "%s=%dx%d" % (n, png_ihdr(d)[0], png_ihdr(d)[1]) for n, d in sorted(state["assets"].items())))
    if errors:
        return 1
    if problems:
        print()
        for p in problems:
            print("  COPY FINDING: " + p)
        print("make_addon: %d copy finding(s) to fix before submission." % len(problems))
        return 1
    if docs["mode"] == "live":
        print("\nblockers carried into alexa/addon.json:")
        for b in state["addon"]["blockers"]:
            print("  - " + b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
