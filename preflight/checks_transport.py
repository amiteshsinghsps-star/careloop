"""checks.transport — MCP Streamable-HTTP contract and tool-declaration checks."""
from .context import EXPECTED_INTENTS, PROTOCOLS
from . import policy as P
from .schema import validate

SECTION = "transport"

# intents that must declare themselves read-only on the wire
READ_ONLY_INTENTS = ("about_careloop", "get_care_plan", "explain_instruction", "list_people",
                     "todays_plan", "what_changed", "prepare_next_visit", "daily_digest")
DESTRUCTIVE_INTENTS = ("delete_instruction",)

# (tool, arguments builder) whose results must match the declared outputSchema
SCHEMA_TARGETS = ("about_careloop", "get_care_plan", "todays_plan", "list_people", "daily_digest",
                  "what_changed", "prepare_next_visit")


def run(ctx, rep):
    initialize_handshake(ctx, rep)
    session_semantics(ctx, rep)
    protocol_version_header(ctx, rep)
    accept_and_origin(ctx, rep)
    get_is_refused(ctx, rep)
    notifications(ctx, rep)
    batch_and_parse(ctx, rep)
    tools_list(ctx, rep)


# --------------------------------------------------------------------- initialize
def initialize_handshake(ctx, rep):
    cid = "init-handshake"
    problems, sids = [], []
    for pv in PROTOCOLS:
        r = ctx.client.initialize(pv)
        if rep.halted:
            return
        if r.status == 401:
            rep.skip(cid, SECTION, "the endpoint requires a bearer token before initialize "
                                   "(401 + %s): pass --token" % (r.header("WWW-Authenticate") or "401"))
            return
        if r.status != 200:
            problems.append("initialize(%s) -> HTTP %d" % (pv, r.status))
            continue
        doc = r.json() or {}
        res = doc.get("result") or {}
        sids.append(r.header("MCP-Session-Id"))
        if res.get("protocolVersion") != pv:
            problems.append("asked %s, answered %r" % (pv, res.get("protocolVersion")))
        info = res.get("serverInfo") or {}
        if not (isinstance(info, dict) and info.get("name") and "version" in info):
            problems.append("serverInfo missing/incomplete: %r" % (info,))
        caps = res.get("capabilities")
        if not isinstance(caps, dict) or "tools" not in caps:
            problems.append("capabilities must list at least 'tools', got %r" % (sorted(caps or {}),))
    # an unknown request must fall back to a supported version, never echo garbage
    r = ctx.client.initialize("1999-01-01")
    if r.status == 200:
        got = ((r.json() or {}).get("result") or {}).get("protocolVersion")
        if r.json() and got not in PROTOCOLS:
            problems.append("unknown protocol negotiated as %r" % got)
        sids.append(r.header("MCP-Session-Id"))
    if problems:
        rep.bad(cid, SECTION, "; ".join(problems[:4]))
        return
    ctx.session = next((s for s in sids if s), None)
    if not ctx.session:
        rep.bad(cid, SECTION, "handshake answered but issued no session id")
        return
    rep.ok(cid, SECTION, "all %d supported versions negotiated, serverInfo+capabilities present, "
                         "unknown version falls back to %s" % (len(PROTOCOLS), PROTOCOLS[0]))


def session_semantics(ctx, rep):
    cid, cid2 = "session-issued-and-honored", "session-unknown-rejected"
    reason = ctx.unauth_reason("session behaviour cannot be probed without a token")
    if reason and not ctx.session:
        rep.skip(cid, SECTION, reason)
        rep.skip(cid2, SECTION, reason)
        return
    if not ctx.bootstrap_session():
        rep.skip(cid, SECTION, "no session: the endpoint refused initialize without credentials")
        rep.skip(cid2, SECTION, "no session: the endpoint refused initialize without credentials")
        return
    ok = ctx.call("ping", {}, mid="s-1")
    problems = []
    if ok.status != 200:
        problems.append("a request carrying the issued session id returned %d" % ok.status)
    unknown = ctx.client.call("ping", mid="s-2", session="cl-not-a-session-" + "0" * 8)
    body = unknown.json() or {}
    if unknown.status != 404 or P.err_of(body).get("code") not in (-32001, -32600):
        problems.append("unknown session id -> HTTP %d %s (expected 404/-32001)"
                        % (unknown.status, body.get("error") or body))
    noheader = ctx.client.call("ping", mid="s-3", session="")
    note = ("a missing MCP-Session-Id header is %s"
            % ("rejected" if noheader.status in (400, 404) else "tolerated by this server"))
    if problems:
        rep.bad(cid, SECTION, "; ".join(problems))
    else:
        rep.ok(cid, SECTION, "session id %s... is honoured on later requests; %s" %
                          (ctx.session[:10], note))
    if unknown.status == 404:
        rep.ok(cid2, SECTION, "an unrecognised session id is refused with 404/-32001, asking to re-initialize")
    else:
        rep.bad(cid2, SECTION, "unknown session id accepted with HTTP %d" % unknown.status)


def protocol_version_header(ctx, rep):
    cid = "protocol-version-header-enforced"
    if not ctx.session:
        rep.skip(cid, SECTION, ctx.unauth_reason("needs an initialised session")
                or "no session available")
        return
    r = ctx.client.call("ping", mid="pv-1", session=ctx.session, protocol="1999-01-01")
    good = ctx.client.call("ping", mid="pv-2", session=ctx.session, protocol=PROTOCOLS[0])
    body = r.json() or {}
    if r.status == 400 and "MCP-Protocol-Version" in str(body.get("error", {}).get("message", "")) \
            and good.status == 200:
        rep.ok(cid, SECTION, "a bad MCP-Protocol-Version header is refused with 400, a supported one passes")
    else:
        rep.bad(cid, SECTION, "bad version -> HTTP %d %r (expected 400 naming the header)"
                % (r.status, body.get("error") or body))


def accept_and_origin(ctx, rep):
    cid, cid2 = "accept-negotiation-enforced", "origin-check-enforced"
    r = ctx.client.rpc({"jsonrpc": "2.0", "id": "a-1", "method": "ping"},
                       headers={"Accept": "text/plain"}, session=ctx.session)
    if r.status == 406:
        rep.ok(cid, SECTION, "an Accept header that cannot carry JSON is refused with 406")
    elif r.status == 401 and not ctx.authenticated():
        rep.skip(cid, SECTION, "endpoint requires a token first (401 before the Accept rule)")
    else:
        rep.bad(cid, SECTION, "Accept: text/plain -> HTTP %d (expected 406)" % r.status)

    o = ctx.client.rpc({"jsonrpc": "2.0", "id": "o-1", "method": "ping"},
                       headers={"Origin": "https://evil-preflight.example"}, session=ctx.session)
    ok_origin = ctx.client.rpc({"jsonrpc": "2.0", "id": "o-2", "method": "ping"},
                               headers={"Origin": ctx.client.base}, session=ctx.session)
    if o.status == 403 and ok_origin.status in (200, 202, 401):
        rep.ok(cid2, SECTION, "a foreign Origin is refused with 403 (forbidden_origin); "
                              "the endpoint's own Origin passes")
    elif o.status == 401 and not ctx.authenticated():
        rep.skip(cid2, SECTION, "endpoint requires a token first (401 before the Origin rule)")
    else:
        rep.bad(cid2, SECTION, "Origin: https://evil-preflight.example -> HTTP %d (expected 403)" % o.status)


def get_is_refused(ctx, rep):
    cid = "get-on-mcp-refused"
    r = ctx.client.get("/mcp", headers={"Accept": "text/event-stream"})
    allow = (r.header("Allow") or "").upper()
    if r.status == 405 and "POST" in allow:
        rep.ok(cid, SECTION, "GET /mcp -> 405 with Allow: %s (no SSE stream claimed)" % r.header("Allow"))
    else:
        rep.bad(cid, SECTION, "GET /mcp -> %d, Allow: %r (expected 405 + Allow: POST)"
                % (r.status, r.header("Allow")))


def notifications(ctx, rep):
    cid = "notification-202-no-body"
    r = ctx.client.rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                       session=ctx.session)
    if r.status == 202 and r.body == b"":
        rep.ok(cid, SECTION, "a JSON-RPC notification answers 202 with an empty body")
    elif r.status == 401 and not ctx.authenticated():
        rep.skip(cid, SECTION, ctx.unauth_reason("notifications need a token"))
    else:
        rep.bad(cid, SECTION, "notification -> HTTP %d with %d body bytes (expected 202 / 0 bytes)"
                % (r.status, len(r.body)))


def batch_and_parse(ctx, rep):
    cid, cid2 = "batch-requests-refused", "parse-error-code"
    b = ctx.client.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 2, "method": "ping"}], session=ctx.session)
    body = b.json() or {}
    msg = str(P.err_of(body).get("message", ""))
    if b.status == 400 and "batch" in msg.lower():
        rep.ok(cid, SECTION, "a JSON-RPC batch is refused with -32600 and says so in the message")
    elif b.status == 401 and not ctx.authenticated():
        rep.skip(cid, SECTION, ctx.unauth_reason("batch refusal is only visible once authorised"))
    else:
        rep.bad(cid, SECTION, "batch -> HTTP %d %r (expected 400 mentioning batch)" % (b.status, body))

    p = ctx.client.rpc("{ this is not json", session=ctx.session)
    pdoc = p.json() or {}
    if p.status == 400 and P.err_of(pdoc).get("code") == -32700:
        rep.ok(cid2, SECTION, "malformed JSON -> 400 with JSON-RPC -32700 Parse error")
    elif p.status == 401 and not ctx.authenticated():
        rep.skip(cid2, SECTION, ctx.unauth_reason("parse error is only visible once authorised"))
    else:
        rep.bad(cid2, SECTION, "malformed JSON -> HTTP %d %r (expected 400 / -32700)"
                % (p.status, pdoc.get("error") or pdoc))


# --------------------------------------------------------------------- tools/list
def tools_list(ctx, rep):
    cid, cid2 = "tools-list-complete", "tool-annotations-honest"
    r = ctx.call("tools/list", {}, mid="tl-1")
    if r.status == 401 and not ctx.authenticated():
        rep.skip(cid, SECTION, ctx.unauth_reason("the tool list itself needs a token"))
        rep.skip(cid2, SECTION, ctx.unauth_reason("the tool list itself needs a token"))
        return
    doc = r.json() or {}
    tools = ((doc.get("result") or {}).get("tools")) or []
    ctx.tools = tools
    ctx.tool_names = [t.get("name", "") for t in tools]
    problems = []
    if r.status != 200 or not tools:
        problems.append("tools/list -> HTTP %d, %d tools" % (r.status, len(tools)))
    else:
        missing = sorted(set(EXPECTED_INTENTS) - {t.get("name") for t in tools})
        if missing:
            problems.append("missing intents %s" % ", ".join(missing))
        if len(tools) != len(set(t.get("name") for t in tools)):
            problems.append("duplicate tool names")
        others = sorted({t.get("name") for t in tools} - set(EXPECTED_INTENTS))
        for t in tools:
            name = t.get("name", "?")
            if not t.get("description"):
                problems.append("%s: no description" % name)
            ins = t.get("inputSchema")
            if not isinstance(ins, dict) or ins.get("type") != "object":
                problems.append("%s: inputSchema missing or not an object schema" % name)
            if isinstance(ins, dict) and ins.get("additionalProperties") is not False:
                problems.append("%s: inputSchema does not close unknown parameters" % name)
            if not isinstance(t.get("outputSchema"), dict) or not t.get("outputSchema"):
                problems.append("%s: no outputSchema declared" % name)
            for bad in P.description_hygiene(t.get("description", ""), ctx.tool_names or EXPECTED_INTENTS):
                problems.append("%s description: %s" % (name, bad))
        if others:
            problems.append("unexpected extra intents %s" % ", ".join(others))
    if problems:
        rep.bad(cid, SECTION, "; ".join(problems[:4]))
    else:
        rep.ok(cid, SECTION, "%d intents, each with name/description/inputSchema/outputSchema; "
                             "descriptions carry no tool names, JSON keys or ids" % len(tools))

    # annotations: an empty list (no credentials, or a rejected token) is not evidence
    # about the server's annotations, so it cannot be audited.
    if not tools:
        rep.skip(cid2, SECTION, "no tool definitions to inspect (tools/list -> HTTP %d)" % r.status)
        return
    by = {t.get("name"): t for t in tools}
    ann_problems = []
    for name in DESTRUCTIVE_INTENTS:
        a = (by.get(name) or {}).get("annotations") or {}
        if a.get("destructiveHint") is not True:
            ann_problems.append("%s must declare destructiveHint=true (got %r)" % (name, a))
        if a.get("readOnlyHint") is True:
            ann_problems.append("%s is destructive but also claims readOnlyHint" % name)
    for name in READ_ONLY_INTENTS:
        a = (by.get(name) or {}).get("annotations") or {}
        if a.get("readOnlyHint") is not True:
            ann_problems.append("%s should declare readOnlyHint=true (got %r)" % (name, a))
        if a.get("destructiveHint") is True:
            ann_problems.append("%s claims readOnlyHint but is marked destructive" % name)
    for name, t in by.items():
        a = t.get("annotations") or {}
        if a.get("readOnlyHint") is True and a.get("destructiveHint") is True:
            ann_problems.append("%s declares both read-only and destructive" % name)
    if ann_problems:
        rep.bad(cid2, SECTION, "; ".join(ann_problems[:4]))
    else:
        rep.ok(cid2, SECTION, "delete_instruction is destructive, %d read intents declare readOnlyHint, "
                              "no tool claims both" % len(READ_ONLY_INTENTS))


# ----------------------------------------------------------------- structuredContent
def run_after_seed(ctx, rep):
    """tools/call results must satisfy the schema each tool declared (needs a live plan)."""
    cid = "structured-content-conforms"
    if not ctx.tools or not ctx.session:
        rep.skip(cid, SECTION, ctx.unauth_reason("tools/call needs a token")
                 or "no tool declarations to compare against")
        return
    schemas = {t["name"]: t.get("outputSchema") or {} for t in ctx.tools if t.get("name")}
    problems, checked = [], 0
    for name in SCHEMA_TARGETS:
        if name not in schemas:
            continue
        args = {}
        if ctx.person and "person" in ((next((t for t in ctx.tools if t["name"] == name), {})
                                        .get("inputSchema") or {}).get("properties") or {}):
            args["person"] = ctx.person
        r = ctx.tool(name, args, mid="sc-" + name)
        doc = r.json() or {}
        result = doc.get("result") or {}
        if r.status != 200 or "result" not in doc:
            problems.append("%s -> HTTP %d %r" % (name, r.status, P.err_of(doc).get("code")))
            continue
        content = result.get("content")
        if not isinstance(content, list) or not content or not all(
                isinstance(c, dict) and c.get("type") and isinstance(c.get("text"), str)
                for c in content):
            problems.append("%s: content[] is not a list of {type,text} blocks" % name)
        sc = result.get("structuredContent")
        if not isinstance(sc, dict):
            problems.append("%s: no structuredContent object" % name)
            continue
        if result.get("isError") is True:
            continue                      # a plain-language tool error is a legal answer
        checked += 1
        for v in validate(sc, schemas[name], name):
            problems.append("%s: %s" % (name, v))
    if problems:
        rep.bad(cid, SECTION, "; ".join(problems[:5]))
    elif checked:
        rep.ok(cid, SECTION, "%d/%d probed tools/call returned content[] plus structuredContent "
                             "conforming to the declared outputSchema" % (checked, len(SCHEMA_TARGETS)))
    else:
        rep.skip(cid, SECTION, "every probed call answered as a tool error; nothing to validate")
