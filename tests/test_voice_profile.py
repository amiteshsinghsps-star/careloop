"""Speaker-scoped answers via a *simulated* voice profile — the service half.

A device ASSERTS who is speaking by putting two slash-namespaced keys in the MCP
`tools/call` request's `params._meta`. The SERVICE decides what that person may hear, from the
boundary the household stored, and records every refusal in the household's own ledger. Nothing
here is biometrics: the claim is labelled simulated (`careloop-simulated/1`) and the device decides
nothing (the artifact says `deviceDecidesScope: false`). Every sentence asserted below is a
`policy_guard` constant, so a reworded refusal fails the test rather than the prose."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from types import SimpleNamespace

import pytest

from careloop import policy_guard as pg, store
from careloop.config import Settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIME_JSON = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

# Synthetic ADULT visit transcripts. Each carries one `diet` instruction (rules confidence 0.6, so
# needs_confirm) alongside confident medication/monitor/appointment items — the diet sentence is what
# makes the pending-held gate reachable. The follow-up appointment and blood-pressure lines mirror
# the fixtures in tests/conftest.py and tests/test_http.py.
MOTHER_TRANSCRIPT = ("Doctor: Your haemoglobin is low. Take 200 mg iron after dinner every day for two "
                     "months. Doctor: Eat a salad with protein at lunch every day. Nurse: Check the blood "
                     "pressure every morning and write it down. Doctor: Come back after six weeks.")
FATHER_TRANSCRIPT = ("Doctor: Your blood pressure is high. Take one tablet of amlodipine in the morning "
                     "every day. Doctor: Have a light soup and walk for twenty minutes daily. Nurse: Check "
                     "the blood pressure every morning. Doctor: Come back after six weeks.")

# Words that appear only inside the transcripts, never in a refusal event (whose fields are a
# speaker name, a claim, a tool name, a reason and an `about`), so a leak is detectable.
TRANSCRIPT_WORDS = ["haemoglobin", "iron", "amlodipine", "tablet", "salad", "protein", "soup",
                    "walk", "blood", "pressure", "dinner", "lunch", "morning", "weeks", "clinic"]


def http(method, url, body=None, headers=None, timeout=15):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else (
        body.encode() if isinstance(body, str) else body)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def rpc(base, method, params=None, mid=None):
    payload = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        payload["id"] = mid
    if params is not None:
        payload["params"] = params
    return http("POST", base + "/mcp", payload, MIME_JSON)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn():
    """An isolated server: auth off, demo mode, its own data root, and encryption off so the
    ledger payloads are readable as plaintext (the household is the demo one, CASA01)."""
    port = free_port()
    db = os.path.join("data", f"test-vp-{port}")
    env = dict(os.environ, CARELOOP_AUTH="off", CARELOOP_DEMO="1", PORT=str(port), HOST="127.0.0.1",
               CARELOOP_DB_ROOT=db, CARELOOP_ENCRYPT_FIELDS="0", CARELOOP_LOG="ERROR")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "serve.py"),
                             "--port", str(port), "--auth", "off"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if http("GET", base + "/healthz", timeout=1)[0] == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("voice-profile server did not start: " + proc.stdout.read(4000).decode(errors="replace"))
    return proc, base, db


def _teardown(proc, db):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(db, ignore_errors=True)


def capture(base, transcript, person):
    status, _, body = http("POST", base + "/api/capture", {"transcript": transcript, "person": person})
    assert status == 200, body
    return json.loads(body)


@pytest.fixture(scope="module")
def vp_server():
    proc, base, db = _spawn()
    mother = capture(base, MOTHER_TRANSCRIPT, "Mother")
    father = capture(base, FATHER_TRANSCRIPT, "Father")
    yield SimpleNamespace(base=base, db=db, mother=mother, father=father)
    _teardown(proc, db)


@pytest.fixture()
def fresh_server():
    """A brand-new server whose MCP conversation context is still empty, so a household-level read
    genuinely resolves to `concerned = None` rather than inheriting a settled person."""
    proc, base, db = _spawn()
    capture(base, MOTHER_TRANSCRIPT, "Mother")
    capture(base, FATHER_TRANSCRIPT, "Father")
    yield SimpleNamespace(base=base, db=db)
    _teardown(proc, db)


def claim(name):
    """The two keys a device sends to assert `name` is speaking, with the profile value that labels
    the claim as what it is (simulated)."""
    return {pg.SPEAKER_META_KEY: name, pg.VOICE_PROFILE_META_KEY: pg.SIMULATED_PROFILE_VALUE}


_OMIT = object()


def call_tool(base, name, arguments=None, meta=_OMIT, mid=1):
    params = {"name": name, "arguments": arguments or {}}
    if meta is not _OMIT:
        params["_meta"] = meta
    status, _, body = rpc(base, "tools/call", params, mid=mid)
    assert status == 200, body
    return json.loads(body)["result"]


def refusals(db_root):
    """Every `speaker_refused` event in the household's ledger, read straight from its cell (the
    companion ledger route surfaces only the event type, not the payload). Restores the store
    globals so the shared module server keeps running in its own process."""
    prev = (store._global, store._settings)
    store.init(Settings(env={"CARELOOP_DB_ROOT": db_root, "CARELOOP_AUTH": "off",
                             "CARELOOP_ENCRYPT_FIELDS": "0", "CARELOOP_FAMILY_PIN": "481542"}))
    try:
        hh = store.global_db().household_by_code("CASA01")
        cell = store.open_cell(hh["hid"], encrypt=False)
        try:
            rows = cell.conn.execute(
                "SELECT seq, type, payload FROM events WHERE type='speaker_refused' ORDER BY seq").fetchall()
            return [json.loads(cell.event_body(r)) for r in rows]
        finally:
            cell.close()
    finally:
        store._global, store._settings = prev


def assert_refusal(res, message, next_step, reason):
    assert res["isError"] is True, res
    assert res["content"][0]["text"] == (message + " " + next_step).strip(), res["content"][0]["text"]
    sc = res["structuredContent"]
    assert sc["speaker_refused"] == reason, sc
    # pre/post gate returns only the refusal — nothing of the plan was ever read out
    assert set(sc.keys()) == {"error", "speaker_refused"}, sc


# ================================================================ the ordinary path
def test_no_claim_answers_the_household_exactly_as_before(vp_server):
    """A call that claims nothing (no `params._meta`, or an empty one) is untouched by the gate:
    same result, same spoken plan, no refusal."""
    with_empty = call_tool(vp_server.base, "get_care_plan", {"person": "Father"}, meta={})
    without_meta = call_tool(vp_server.base, "get_care_plan", {"person": "Father"})
    assert with_empty["isError"] is False, with_empty
    assert without_meta["isError"] is False, without_meta
    assert "speaker_refused" not in with_empty["structuredContent"], with_empty["structuredContent"]
    assert "speaker_refused" not in without_meta["structuredContent"], without_meta["structuredContent"]
    assert with_empty["content"][0]["text"] == without_meta["content"][0]["text"], "plan text drifted"


def test_valid_claim_within_boundary_gets_the_ordinary_answer(vp_server):
    """A device claims a person this household has, and whose stored boundary covers them (Mother's
    boundary names Mother): the answer is identical to the same call with no claim."""
    plan = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"})
    confident = next(i["ref"] for i in plan["structuredContent"]["items"] if not i["needs_confirm"])
    control = call_tool(vp_server.base, "explain_instruction", {"ref": confident})
    claimed = call_tool(vp_server.base, "explain_instruction", {"ref": confident}, meta=claim("Mother"), mid=2)
    assert claimed["isError"] is False, claimed
    assert "speaker_refused" not in claimed["structuredContent"], claimed["structuredContent"]
    assert claimed["structuredContent"] == control["structuredContent"], "an in-scope claim changed the answer"


# ================================================================ a claim that buys nothing on its own
def test_claim_naming_a_stranger_is_refused_unknown(vp_server):
    """The claim resolves to no person or alias this household has, so it is refused before the tool
    runs — the plan is not read at all."""
    res = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"}, meta=claim("Ghost"), mid=3)
    assert_refusal(res, pg.SPEAKER_UNKNOWN, pg.SPEAKER_UNKNOWN_NEXT, "speaker-unknown")
    shown = [e for e in refusals(vp_server.db) if e.get("reason") == "speaker-unknown"]
    assert shown, "the ledger recorded nothing for a refused unknown speaker"
    assert shown[-1]["claim"] == "Ghost" and shown[-1]["tool"] == "get_care_plan", shown[-1]


def test_claim_without_the_profile_label_is_refused_bad_profile(vp_server):
    """A device cannot just say whoever it likes: a bare `careloop/speaker` with no profile label —
    or a value that isn't the simulated one — is refused as unrecognised."""
    no_profile = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"},
                           meta={pg.SPEAKER_META_KEY: "Mother"}, mid=4)
    assert_refusal(no_profile, pg.SPEAKER_BAD_PROFILE, pg.SPEAKER_BAD_PROFILE_NEXT, "profile-unrecognised")
    wrong_profile = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"},
                              meta={pg.SPEAKER_META_KEY: "Mother", pg.VOICE_PROFILE_META_KEY: "whoever-says-so/9"},
                              mid=41)
    assert wrong_profile["structuredContent"]["speaker_refused"] == "profile-unrecognised", wrong_profile


def test_person_outside_the_boundary_is_refused_and_a_write_changes_nothing(vp_server):
    """Mother's boundary covers only Mother. Asking about Father is out of scope, and a WRITE
    attempted out of scope never runs: the pre-flight gate refuses before the tool, so the plan is
    byte-for-byte unchanged afterwards."""
    oos = call_tool(vp_server.base, "get_care_plan", {"person": "Father"}, meta=claim("Mother"), mid=5)
    assert_refusal(oos, pg.SPEAKER_OUT_OF_SCOPE, pg.SPEAKER_OUT_OF_SCOPE_NEXT, "out-of-scope")
    before = call_tool(vp_server.base, "get_care_plan", {"person": "Father"}, mid=50)
    n_before = len(before["structuredContent"]["items"])
    refused_write = call_tool(vp_server.base, "add_note", {"person": "Father",
                            "text": "Remember to bring the medicine list"}, meta=claim("Mother"), mid=51)
    assert_refusal(refused_write, pg.SPEAKER_OUT_OF_SCOPE, pg.SPEAKER_OUT_OF_SCOPE_NEXT, "out-of-scope")
    after = call_tool(vp_server.base, "get_care_plan", {"person": "Father"}, mid=52)
    assert len(after["structuredContent"]["items"]) == n_before, "an out-of-scope write mutated the plan"


def test_partial_boundary_cannot_answer_for_the_whole_household(fresh_server):
    """A household-level read has no person argument, so it concerns everyone. A boundary that
    covers only one of the two people cannot stand in for the whole household."""
    res = call_tool(fresh_server.base, "list_people", {}, meta=claim("Mother"), mid=6)
    assert_refusal(res, pg.SPEAKER_OUT_OF_SCOPE, pg.SPEAKER_OUT_OF_SCOPE_NEXT, "out-of-scope")
    shown = [e for e in refusals(fresh_server.db) if e.get("reason") == "out-of-scope"]
    assert shown and shown[-1]["about"] == "everyone", shown


def test_speaker_with_no_stored_boundary_is_answered_in_full(vp_server):
    """Backwards compatibility: a household person with no boundary entry is bounded by nothing, so
    they are answered exactly as the household always was — every pending item, including a
    not-yet-confirmed one."""
    control = call_tool(vp_server.base, "get_care_plan", {"person": "Father"})
    claimed = call_tool(vp_server.base, "get_care_plan", {"person": "Father"}, meta=claim("Father"), mid=7)
    assert claimed["isError"] is False, claimed
    assert "speaker_refused" not in claimed["structuredContent"], claimed["structuredContent"]
    assert len(claimed["structuredContent"]["items"]) == len(control["structuredContent"]["items"])
    assert any(i["needs_confirm"] for i in claimed["structuredContent"]["items"]), \
        "no pending item to prove an unbounded speaker sees everything"


# ================================================================ pending held (read-only only)
def test_pending_instruction_is_held_from_a_speaker_who_must_not_hear_it(vp_server):
    """The post-read gate: Mother's boundary says may_hear_pending false. First prove the answer
    really did carry an unconfirmed item, then show the same read as Mother is withheld."""
    open_plan = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"})
    assert pg.reveals_pending(open_plan["structuredContent"]), "the read was not actually pending"
    res = call_tool(vp_server.base, "get_care_plan", {"person": "Mother"}, meta=claim("Mother"), mid=8)
    assert_refusal(res, pg.SPEAKER_PENDING_HELD, pg.SPEAKER_PENDING_HELD_NEXT, "pending-held")
    # the gate is read-only: a WRITE in scope is never withheld, even under may_hear_pending false
    write = call_tool(vp_server.base, "add_note", {"person": "Mother",
                    "text": "Bring the list to the clinic"}, meta=claim("Mother"), mid=81)
    assert write["isError"] is False, "the pending gate wrongly reached a non-readOnlyHint tool"


def test_pending_gate_applies_only_to_readonly_tools():
    """Direct check on the gate's own condition: it is guarded by `readOnlyHint`, so a response with
    a pending item from a write tool is not a candidate; `reveals_pending` is the shared test for the
    read side."""
    pending = {"person": "Mother", "items": [{"ref": "i-abc123", "needs_confirm": True}]}
    assert pg.reveals_pending(pending), "a needs_confirm item must read as pending"
    assert not pg.reveals_pending({"person": "Mother", "items": [{"ref": "i-abc123", "needs_confirm": False}]})
    for defn in json.loads(json.dumps([{"name": "add_note", "annotations": {"readOnlyHint": False}},
                                       {"name": "get_care_plan", "annotations": {"readOnlyHint": True}}])):
        if defn["name"] == "add_note":
            assert not defn["annotations"].get("readOnlyHint"), "add_note is not a read — gate skips it"
        else:
            assert defn["annotations"].get("readOnlyHint"), "get_care_plan is the gated read"


# ================================================================ the ledger carries no health text
def test_refusal_ledger_carries_only_scope_fields_and_no_health_text(vp_server):
    """Every refusal is recorded in the household's own ledger, but the event is a claim about
    reach, not about health: it may only carry speaker/claim/tool/reason/about, and never a word from
    the transcript."""
    call_tool(vp_server.base, "get_care_plan", {"person": "Mother"}, meta=claim("Ghost"), mid=900)
    events = refusals(vp_server.db)
    assert events, "no refusal events were recorded at all"
    allowed = {"speaker", "claim", "tool", "reason", "about"}
    for ev in events:
        assert set(ev.keys()) <= allowed, f"ledger event carries extra fields: {sorted(ev)}"
        blob = json.dumps(ev).lower()
        for word in TRANSCRIPT_WORDS:
            assert word not in blob, f"transcript word {word!r} leaked into the ledger"


# ================================================================ resolver (no server)
def test_resolve_claim_is_case_insensitive_and_honours_aliases():
    people = [{"name": "Mother", "aliases": ["mum", "mom"]}, {"name": "Father", "aliases": []}]
    assert pg.resolve_claim({pg.SPEAKER_META_KEY: "mOtHeR",
                             pg.VOICE_PROFILE_META_KEY: pg.SIMULATED_PROFILE_VALUE}, people) == ("ok", "Mother")
    assert pg.resolve_claim({pg.SPEAKER_META_KEY: "MUM",
                             pg.VOICE_PROFILE_META_KEY: pg.SIMULATED_PROFILE_VALUE}, people) == ("ok", "Mother")
    assert pg.resolve_claim({}, people) == ("none", None)
    assert pg.resolve_claim({pg.SPEAKER_META_KEY: "Mother"}, people) == ("bad-profile", None)
    assert pg.resolve_claim({pg.SPEAKER_META_KEY: "Ghost",
                             pg.VOICE_PROFILE_META_KEY: pg.SIMULATED_PROFILE_VALUE}, people) == ("unknown", None)


# ================================================================ drift gate: device artifact vs service
def test_shipped_artifact_declares_the_same_meta_keys():
    """The device's own model must describe exactly this mechanism: two slash-namespaced keys, the
    simulated label, and no real biometrics, no device-side scope decision, no writes."""
    model = json.load(open(os.path.join(ROOT, "alexa", "interaction-model.json"), encoding="utf-8"))
    a = model["voiceProfile"]["assertion"]
    assert a["speakerMetaKey"] == pg.SPEAKER_META_KEY, a
    assert a["profileMetaKey"] == pg.VOICE_PROFILE_META_KEY, a
    assert a["profileValue"] == pg.SIMULATED_PROFILE_VALUE, a
    assert a["simulated"] is True, "the profile claim must be labelled simulated"
    assert a["deviceDecidesScope"] is False, "the device decides nothing about scope"
    assert a["writesAnything"] is False, "the assertion writes nothing"
    assert a["sessionScopedOnly"] is True, "the claim is session-scoped only"
