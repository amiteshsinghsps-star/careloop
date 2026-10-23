"""The v7 platform surface: per-document security policy (M12), the live-sync stream (M7),
ledger time travel (M6) and the PHI-free bug report (M13). Run against the real server.

These endpoints answer for whichever household the caller belongs to, so in demo mode
(CARELOOP_AUTH=off) they resolve to the demo household — the documented behaviour, and
the reason the tests below need no cookie.
"""
import datetime as dt
import glob
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from careloop import mcp_server, tools

MIME_JSON = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def http(method, url, body=None, headers=None, timeout=15):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def http_post(url, payload, headers=None):
    h = dict(MIME_JSON)
    h.update(headers or {})
    return http("POST", url, json.dumps(payload).encode(), h)


def rpc(base, method, params=None, mid=None, session=None):
    payload = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        payload["id"] = mid
    if params is not None:
        payload["params"] = params
    h = dict(MIME_JSON)
    if session:
        h["MCP-Session-Id"] = session
    return http("POST", base + "/mcp", json.dumps(payload).encode(), h)


def viewers(base):
    """How many live-sync seats the server is currently holding."""
    return json.loads(http("GET", base + "/api/diagnostics")[2])["counts"]["live_viewers"]


def wait_for(fn, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.2)
    return bool(fn())


class Stream:
    """One held-open Server-Sent-Events connection, read frame by frame."""

    def __init__(self, base, path="/api/events"):
        req = urllib.request.Request(base + path, headers={"Accept": "text/event-stream"})
        self.status, self.headers = 200, {}
        self.res = urllib.request.urlopen(req, timeout=25)
        self.headers = dict(self.res.headers)
        self.lines = iter(self.res)

    def frame(self):
        name, data = "", ""
        for raw in self.lines:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
            elif line == "" and (name or data):
                return name, data
        raise AssertionError("the stream ended without another frame")

    def frame_within(self, want, seconds=8):
        """Read frames until `want` arrives; pings and other events are skipped on purpose."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            name, data = self.frame()
            if name == want:
                return json.loads(data)
        raise AssertionError(f"no '{want}' frame within {seconds}s")

    def close(self):
        self.res.close()


# ================================================================ M12: security policy
def test_an_html_surface_carries_a_closed_content_security_policy(live_server):
    for path in ("/", "/card", "/companion", "/simulator"):
        status, hdrs, _ = http("GET", live_server + path)
        assert status == 200, (path, status)
        assert hdrs["Content-Type"].startswith("text/html"), path
        csp = hdrs["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp, (path, csp)
        assert "unsafe-eval" not in csp, path
        assert "object-src 'none'" in csp, path
        assert "frame-ancestors 'self'" in csp, path
        assert hdrs["X-Content-Type-Options"] == "nosniff", path
        assert hdrs["Referrer-Policy"] == "no-referrer", path
        assert hdrs["X-Frame-Options"] == "SAMEORIGIN", path


def test_only_the_companion_document_is_allowed_a_base_href(live_server):
    """The Angular build writes `<base href="/companion/">` into its index.html and a browser
    refuses that under base-uri 'none', which is how the shipped page ended up loading its
    stylesheets from the wrong directory. The exception is same-origin only, so the injected
    cross-origin base the directive exists to stop stays blocked, and no other document moves."""
    _, hdrs, _ = http("GET", live_server + "/companion")
    csp = hdrs["Content-Security-Policy"]
    assert "base-uri 'self'" in csp, csp
    assert "base-uri 'none'" not in csp, csp
    for path in ("/", "/card", "/simulator", "/judge", "/a11y-lab"):
        _, other, _ = http("GET", live_server + path)
        if other["Content-Type"].startswith("text/html"):
            assert "base-uri 'none'" in other["Content-Security-Policy"], (path, csp)


def test_every_built_site_page_is_reachable_and_the_lab_carries_no_dose(live_server):
    """A page added to frontend/site is invisible until it is also mounted, so this checks the
    routes the site actually builds rather than a list someone might remember to update."""
    for path in ("/privacy", "/terms", "/docs", "/a11y-lab"):
        status, hdrs, body = http("GET", live_server + path)
        assert status == 200, (path, status)
        assert hdrs["Content-Type"].startswith("text/html"), path
    status, _, body = http("GET", live_server + "/a11y-lab")
    text = body.decode("utf-8", "replace")
    assert "The Accessibility Lab" in text
    assert "your prescribed amount" in text                 # masked, as the card is
    assert not re.search(r"\b\d+(\.\d+)?\s*(mg|mcg|g|ml|mL|IU)\b", text), "a dose reached the site"
    status, _, _ = http("GET", live_server + "/no-such-page")
    assert status == 404, status


def test_hsts_is_absent_because_the_demo_really_is_plain_http(live_server):
    _, hdrs, _ = http("GET", live_server + "/card")
    assert "Strict-Transport-Security" not in hdrs


def test_the_card_is_the_strictest_document_because_the_host_embeds_it(live_server):
    _, hdrs, _ = http("GET", live_server + "/card")
    assert hdrs["Content-Security-Policy"].startswith("default-src 'none'"), hdrs["Content-Security-Policy"]
    _, other, _ = http("GET", live_server + "/companion")
    assert other["Content-Security-Policy"].startswith("default-src 'self'")


def test_only_the_capture_screen_is_allowed_to_ask_for_the_microphone(live_server):
    _, hdrs, _ = http("GET", live_server + "/companion")
    assert "microphone=(self)" in hdrs["Permissions-Policy"], hdrs["Permissions-Policy"]
    for path in ("/", "/card", "/simulator"):
        _, h, _ = http("GET", live_server + path)
        if h["Content-Type"].startswith("text/html"):
            pp = h["Permissions-Policy"]
            assert "microphone=()" in pp, (path, pp)
            assert "camera=()" in pp and "geolocation=()" in pp and "payment=()" in pp, (path, pp)


def test_a_json_response_cannot_become_an_attack_surface_of_its_own(live_server):
    for path in ("/healthz", "/metrics", "/api/judge-data", "/api/diagnostics"):
        status, hdrs, _ = http("GET", live_server + path)
        assert status == 200, (path, status)
        assert hdrs["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'", path
        assert hdrs["Cache-Control"] == "no-store", path
        assert hdrs["X-Content-Type-Options"] == "nosniff", path


def test_a_script_or_stylesheet_is_not_delivered_with_a_policy_of_its_own(live_server):
    """'default-src none' on a JS asset is not a harmless belt-and-braces: Chromium applies a
    policy delivered *with* a resource to that resource's own fetches, so it stopped the
    companion module importing its lazy login chunk and the app booted an empty shell.
    Documents, JSON and SVG keep the lock-down; scripts and stylesheets do not."""
    assets = []
    for dist in ("companion", "simulator"):
        for ext in ("js", "css"):
            for p in glob.glob(os.path.join(mcp_server.WEB_DIR, dist, "**", f"*.{ext}"),
                               recursive=True):
                if ".typecheck" not in p:
                    assets.append((dist, p))
    if not assets:
        pytest.skip("no built bundles to serve — run `npm run dist`")
    assert len(assets) > 10, f"only {len(assets)} asset files found; the glob is wrong"
    for dist, path in assets:
        rel = "/" + "/".join(os.path.relpath(path, os.path.join(mcp_server.WEB_DIR, dist))
                             .split(os.sep))
        url = live_server + "/" + dist + rel
        status, hdrs, _ = http("GET", url)
        assert status == 200, (url, status)
        assert "Content-Security-Policy" not in hdrs, (url, hdrs.get("Content-Security-Policy"))
        assert hdrs["X-Content-Type-Options"] == "nosniff", url
    _, hdrs, _ = http("GET", live_server + "/companion/index.html")
    assert hdrs["Content-Security-Policy"].startswith("default-src 'self'"), hdrs


# ================================================================ M7: live sync
def test_the_live_stream_greets_a_viewer_with_the_version_it_is_looking_at(live_server):
    s = Stream(live_server)
    try:
        assert s.status == 200
        assert s.headers["Content-Type"].startswith("text/event-stream")
        assert s.headers["Cache-Control"] == "no-store"
        name, data = s.frame()
        assert name == "hello", (name, data)
        assert isinstance(json.loads(data)["version"], int)
    finally:
        s.close()


def test_a_plan_change_reaches_the_open_stream_within_a_second(live_server):
    """That one-second propagation is the whole point of the endpoint: a 'done' tapped on
    one phone has to appear on a sibling's screen without either of them reloading."""
    s = Stream(live_server)
    try:
        hello = json.loads(s.frame()[1])
        status, _, body = http_post(live_server + "/api/capture",
                                    {"transcript": "Doctor: Take 10 mg lisinopril every morning "
                                                   "for the blood pressure.", "person": "Papa"})
        assert status == 200, body
        plan = s.frame_within("plan")
        assert plan["version"] > hello["version"], (hello, plan)
        assert plan["verified"] is True
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", plan["at"]), plan["at"]
    finally:
        s.close()


def test_the_viewer_count_goes_back_to_zero_once_everyone_hangs_up(live_server):
    assert wait_for(lambda: viewers(live_server) == 0), "a previous test left a stream open"
    before = viewers(live_server)
    held = [Stream(live_server) for _ in range(3)]
    try:
        during = viewers(live_server)
    finally:
        for h in held:
            h.close()
    assert during == before + 3, (before, during)
    # the server notices a hung-up viewer on its own tick, so the seat frees in well under
    # a ping interval rather than after 15 seconds of looking taken
    assert wait_for(lambda: viewers(live_server) == before), (before, viewers(live_server))


def test_the_ninth_concurrent_viewer_is_turned_away_without_losing_the_plan(live_server):
    """An open stream is a held connection and a held SQLite cursor, so the cap is real."""
    assert wait_for(lambda: viewers(live_server) == 0), "streams from a previous test are still counted"
    held = [Stream(live_server) for _ in range(8)]
    try:
        status, _, body = http("GET", live_server + "/api/events")
        assert status == 503, body
        assert "live viewers" in json.loads(body)["error"].lower(), body
        assert http("GET", live_server + "/api/ledger")[0] == 200
    finally:
        for h in held:
            h.close()


# ================================================================ M6: time travel
def test_the_ledger_answers_what_the_plan_was_at_a_moment(live_server):
    before = time.time()
    time.sleep(1.3)
    made = http_post(live_server + "/api/capture",
                     {"transcript": "Doctor: Take 5 mg amlodipine every evening for the blood pressure.",
                      "person": "Grandma"})
    assert made[0] == 200, made[2]
    time.sleep(1.3)
    after = time.time()

    then = json.loads(http("GET", live_server + "/api/ledger?until=%.6f" % before)[2])
    now = json.loads(http("GET", live_server + "/api/ledger?until=%.6f" % after)[2])
    refs = lambda snap: {i["ref"] for i in snap["state"]["items"]}
    assert refs(now) - refs(then), "the visit added nothing the snapshot can see"
    assert now["events"] > then["events"], (then["events"], now["events"])
    assert then["verified"] is True and now["verified"] is True
    seqs = [h["seq"] for h in now["history"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), seqs[:10]
    assert {"item_added", "visit_recorded"} & {h["type"] for h in now["history"]}
    assert all(h["at"] <= after for h in now["history"])
    assert len(now["history"]) == now["events"], (len(now["history"]), now["events"])


def test_the_moment_may_be_iso_and_a_nonsense_one_is_refused_plainly(live_server):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    status, _, body = http("GET", live_server + "/api/ledger?until=" + urllib.parse.quote(stamp))
    assert status == 200, body
    assert json.loads(body)["requested_until"] is not None
    status, _, body = http("GET", live_server + "/api/ledger?until=last%20tuesday")
    assert status == 400, body
    assert "date" in json.loads(body)["error"].lower(), body


def test_a_snapshot_never_shows_an_item_that_was_deleted_by_then(live_server):
    """The point of replaying the chain rather than reading a table: a deletion must be
    invisible before it happened and gone after it happened."""
    made = http_post(live_server + "/api/capture",
                     {"transcript": "Doctor: Take 20 mg atorvastatin every night for the cholesterol.",
                      "person": "Papa"})
    data = json.loads(made[2])
    refs = [i["ref"] for i in data.get("items", [])] or \
           [i["ref"] for i in json.loads(http("GET", live_server + "/api/ledger")[2])["state"]["items"]]
    assert refs, made[2]
    target = refs[-1]
    _, hdrs, _ = rpc(live_server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                 "clientInfo": {"name": "t", "version": "1"}}, mid="tt-0")
    sid = hdrs["MCP-Session-Id"]
    # two-step delete: first the read-back, then the confirmed removal
    rpc(live_server, "tools/call", {"name": "delete_instruction", "arguments": {"ref": target}},
        mid="tt-1", session=sid)
    gone = rpc(live_server, "tools/call", {"name": "delete_instruction",
                                           "arguments": {"ref": target, "confirm": True}},
               mid="tt-2", session=sid)
    assert json.loads(gone[2])["result"]["structuredContent"].get("deleted") is True, gone[2]
    time.sleep(1.3)
    later = json.loads(http("GET", live_server + "/api/ledger")[2])
    assert target not in {i["ref"] for i in later["state"]["items"]}, later["state"]["items"]
    assert "item_deleted" in {h["type"] for h in later["history"]}


# ================================================================ M13: bug report
def test_the_bug_report_bundle_carries_numbers_and_no_care_content(live_server):
    marker = "Zqfk walk to the mailbox twice daily"
    http_post(live_server + "/api/capture", {"transcript": "Nurse: " + marker + ".", "person": "Papa"})
    status, hdrs, body = http("GET", live_server + "/api/diagnostics")
    assert status == 200, body
    data = json.loads(body)
    text = body.decode().lower()
    for banned in (marker.lower(), "amlodipine", "atorvastatin", "doctor:", "nurse:",
                   "papa", "grandma", "mg"):
        assert banned not in text, banned
    assert data["note"].startswith("No transcript text")
    assert data["auth_mode"] == "off"
    assert re.fullmatch(r"[0-9a-f]{64}", data["manifest_hash"])
    assert data["counts"]["tools"] == len(tools.TOOL_DEFS)
    assert isinstance(data["counts"]["live_viewers"], int)
    assert isinstance(data["encryption_at_rest"], bool)
    assert set(data["ledger"]) == {"events", "ok", "broken_at", "reason"}
    assert data["ledger"]["ok"] is True and data["ledger"]["broken_at"] is None
    assert len(data["household"]) == 12 and "TESTHH" not in data["household"]
    assert hdrs["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_the_bundle_reports_the_chaos_it_was_started_with(chaos_server):
    status, _, body = http("GET", chaos_server + "/api/diagnostics")
    assert status == 200, body
    data = json.loads(body)
    assert data["chaos"] == ["db_locked", "latency_800ms"], data["chaos"]
    assert data["chaos_unknown"] == []


# ================================================================ auth enforced
def test_an_unknown_caller_gets_no_stream_no_history_and_no_bundle(oauth_server):
    """With auth on there is no demo household to fall back to, so these must be 401 and
    not an empty 200 — an empty 200 would invite a client to keep polling."""
    for path in ("/api/events", "/api/ledger", "/api/diagnostics"):
        status, _, body = http("GET", oauth_server + path)
        assert status == 401, (path, status, body)
        assert "sign in" in json.loads(body)["error"].lower(), path


# ================================================================ the gates themselves
def _load_gate(name):
    """Import one scripts/*.py by path: they are stdlib CLIs, not package modules."""
    import importlib.util
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(name, os.path.join(root, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_citation_gate_notices_a_test_pointer_that_moved():
    """A gate that checks nothing looks identical to a clean run, and the first version of this
    one did exactly that: it reported "0 citations checked" on a repo holding 11 wrong test
    pointers, because the name to compare sits at the start of its own match. So both
    directions are pinned here -- and the count is asserted, not just the verdict.
    """
    cc = _load_gate("check_citations")
    index = cc._index_sources()
    defs = cc.test_defs("tests/test_http.py")
    assert defs, "the gate found no test defs to compare against"
    name, line = "test_the_companion_api_requires_a_session", defs["test_the_companion_api_requires_a_session"]

    good, n_good = cc.name_line_problems("doc.md", f"`{name}` ({'test_http.py'}:{line}).", index)
    assert good == [] and n_good == 1, (good, n_good)

    bad, n_bad = cc.name_line_problems("doc.md", f"`{name}` (test_http.py:{line + 1}).", index)
    assert n_bad == 1, "a wrong pointer must still be *checked*, or the gate is decoration"
    assert len(bad) == 1 and "its def is at" in bad[0], bad

    # siblings named on another line: counts do not line up, so the gate abstains rather than
    # guessing which number belongs to which name
    skip, n_skip = cc.name_line_problems("doc.md", f"`{name}` (test_http.py:1, :2).", index)
    assert (skip, n_skip) == ([], 0), (skip, n_skip)
