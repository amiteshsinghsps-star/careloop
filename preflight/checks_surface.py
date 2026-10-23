"""checks.surface — round-trip latency budget and the mcp-app card resource."""
import re

from .context import CARD_URI

SECTION_PERF = "performance"
SECTION_CARD = "card"
P95_BUDGET_MS = 500.0
SAMPLES = 20


def run_performance(ctx, rep):
    cid = "p95-under-500ms"
    if not ctx.session:
        return rep.skip(cid, SECTION_PERF, ctx.unauth_reason("tools/call latency needs a token")
                        or "no session available")
    args = {"person": ctx.person} if ctx.person else {}
    times, errors = [], []
    for i in range(SAMPLES):
        r = ctx.tool("get_care_plan", args, mid="perf-%d" % i)
        times.append(ctx.client.last_ms)
        if r.status != 200:
            errors.append("call %d -> HTTP %d" % (i, r.status))
        else:
            doc = r.json() or {}
            if (doc.get("result") or {}).get("isError") is True:
                errors.append("call %d answered as a tool error" % i)
    if errors:
        return rep.bad(cid, SECTION_PERF, "; ".join(errors[:3]))
    p50, p95, p99, mx = percentiles(times)
    detail = "n=%d client-side round trips: p50 %.0f ms, p95 %.0f ms, p99 %.0f ms, max %.0f ms" % (
        SAMPLES, p50, p95, p99, mx)
    if p95 >= P95_BUDGET_MS:
        return rep.bad(cid, SECTION_PERF, "%s -- budget is %d ms" % (detail, int(P95_BUDGET_MS)))
    return rep.ok(cid, SECTION_PERF, "%s (budget %d ms, measured locally)" % (detail, int(P95_BUDGET_MS)))


def percentiles(values):
    """Nearest-rank percentiles, computed here: no external service involved."""
    s = sorted(values)
    if not s:
        return 0.0, 0.0, 0.0, 0.0

    def at(q):
        return s[min(len(s) - 1, int(q * len(s)))]
    return at(0.50), at(0.95), at(0.99), s[-1]


# ------------------------------------------------------------------ card resource
def run_card(ctx, rep):
    declared(ctx, rep)
    self_contained(ctx, rep)


def declared(ctx, rep):
    cid = "card-resource-declared"
    if not ctx.session:
        return rep.skip(cid, SECTION_CARD, ctx.unauth_reason("resources/list needs a token")
                        or "no session available")
    r = ctx.call("resources/list", {}, mid="card-1")
    doc = r.json() or {}
    resources = ((doc.get("result") or {}).get("resources")) or []
    match = [x for x in resources if x.get("uri") == CARD_URI]
    if r.status == 200 and match and match[0].get("mimeType") == "text/html;profile=mcp-app":
        return rep.ok(cid, SECTION_CARD, "resources/list declares %s as text/html;profile=mcp-app"
                      % CARD_URI)
    return rep.bad(cid, SECTION_CARD, "expected one %s resource with the mcp-app profile, got %r"
                   % (CARD_URI, [x.get("uri") for x in resources]))


REMOTE_CSS = re.compile(r"<link\b[^>]*?(?:stylesheet|icon|manifest)[^>]*?href\s*=\s*[\"']?"
                        r"(?:https?:)?//", re.I)
SCRIPT_SRC = re.compile(r"<script\b[^>]*\ssrc\s*=", re.I)
CSS_URL = re.compile(r"url\(\s*[\"']?(?:https?:)?//", re.I)
FONT_FACE_REMOTE = re.compile(r"@font-face[^}]*?src\s*:[^}]*?(?:https?:)?//", re.I)
REMOTE_ASSET_TAG = re.compile(r"<(?:img|iframe|source|video|audio|embed)\b[^>]*?(?:src|srcset)\s*="
                              r"\s*[\"']?(?:https?:)?//", re.I)
DISCLAIMER_MARK = re.compile(r"not medical advice|disclaimer", re.I)


def self_contained(ctx, rep):
    cid = "card-self-contained-html"
    if not ctx.session:
        return rep.skip(cid, SECTION_CARD, ctx.unauth_reason("resources/read needs a token")
                        or "no session available")
    r = ctx.call("resources/read", {"uri": CARD_URI}, mid="card-2")
    doc = r.json() or {}
    contents = ((doc.get("result") or {}).get("contents")) or []
    if len(contents) != 1:
        return rep.bad(cid, SECTION_CARD, "resources/read returned %d contents (expected exactly one) "
                                       "or an error: %r" % (len(contents), (doc.get("error") or {})))
    item = contents[0]
    html = item.get("text") or ""
    problems = []
    if item.get("mimeType") != "text/html;profile=mcp-app":
        problems.append("mimeType is %r" % item.get("mimeType"))
    if not html.lstrip().lower().startswith("<!doctype html"):
        problems.append("the document is not a complete HTML page (no doctype)")
    if SCRIPT_SRC.search(html):
        problems.append("an external <script src=...> tag: %s" % SCRIPT_SRC.search(html).group(0)[:60])
    if REMOTE_CSS.search(html):
        problems.append("a remote stylesheet: %s" % REMOTE_CSS.search(html).group(0)[:60])
    if CSS_URL.search(html):
        problems.append("a CSS url() pointing off-box: %s" % CSS_URL.search(html).group(0)[:60])
    if FONT_FACE_REMOTE.search(html):
        problems.append("a remote @font-face source")
    if REMOTE_ASSET_TAG.search(html):
        problems.append("a remote media/iframe asset: %s" % REMOTE_ASSET_TAG.search(html).group(0)[:60])
    if not DISCLAIMER_MARK.search(html):
        problems.append("the card carries no not-medical-advice note")
    if problems:
        return rep.bad(cid, SECTION_CARD, "; ".join(problems[:4]))
    return rep.ok(cid, SECTION_CARD, "one self-contained HTML document (%d KB), inline script and "
                                     "style only, no external script/stylesheet/font URL, disclaimer "
                                     "on the card" % (max(1, len(html) // 1024)))
