"""Tests for the headless virtual Alexa+ device driver (scripts/alexa_device.py).

Covers: the ASK envelope builders produce the camelCase wire JSON that ask_sdk_core's own
DefaultSerializer produces for the same model objects (and that the SDK reads ours back),
SSML escaping, dialog-directive selection (elicitation vs confirmation vs plain), that the
independent dose-leak check CAN fail (a planted amount is caught), the spoken word cap,
the trace schema and the no-dose-in-trace rule, and one end-to-end golden-plan run
against the real voice_server fixture (the protocol is never mocked).
"""
import json
import os
import re

import pytest

import scripts.alexa_device as ad

# The keys every turn / the trace itself must carry (see scripts/alexa_device.py trace). The last
# three per-turn fields are the two turns the artifact answers without a call: how the answer was
# spoken, what the device claimed about who spoke, and what the service refused — named exactly as
# the browser Device Lab's own `exportTrace` names them, because --compare lines the two up.
TURN_KEYS = {"index", "utterance", "state_in", "state_out", "request_envelope", "mcp",
             "response_envelope", "spoken_ssml", "spoken_text", "words", "ms", "called",
             "delivery", "meta", "refused"}
TRACE_KEYS = {"kind", "version", "scenario", "created_at", "device", "server", "turns",
              "checks", "summary"}
SUMMARY_KEYS = {"turns", "checks_passed", "checks_failed", "dose_leaks", "masked_fields",
                "calls", "elicits", "confirmations_asked", "confirmations_refused",
                "reminders_armed", "reminders",
                "delivery_turns", "speaker_turns", "claims_sent", "refusals"}

# The comparison tests need Amazon's packages installed; the driver itself must not.
requires_sdk = pytest.mark.skipif(
    not ad.HAVE_SDK, reason="ask_sdk_model / ask_sdk_core are optional and not installed here")


# ================================================================ SDK shape equality
@requires_sdk
def test_launch_envelope_matches_the_sdk_wire_json():
    ts = "2026-01-01T00:00:00Z"
    hand = ad.build_launch_envelope("r-1", ts)
    assert hand == ad.build_launch_envelope_sdk("r-1", ts)
    assert set(hand) == {"version", "session", "context", "request"}
    assert set(hand["session"]) == {"new", "sessionId", "user", "attributes", "application"}
    assert set(hand["request"]) == {"type", "requestId", "timestamp", "locale"}
    assert "System" in hand["context"]                    # capital S, as on the wire
    assert ad.sdk_round_trip(hand).request.object_type == "LaunchRequest"


@requires_sdk
def test_intent_envelope_matches_the_sdk_wire_json():
    ts = "2026-01-01T00:00:00Z"
    slots = {"Person": "Papa"}
    hand = ad.build_intent_envelope("r-2", ts, "WhatsMyPlanIntent", slots)
    assert hand == ad.build_intent_envelope_sdk("r-2", ts, "WhatsMyPlanIntent", slots)
    # no utterance rides on an Intent, and a slot with nothing in it sends only its name
    assert "utterance" not in hand["request"]["intent"]
    empty = ad.build_intent_envelope("r-3", ts, "WhatsMyPlanIntent", {"Person": None})
    assert empty["request"]["intent"]["slots"] == {"Person": {"name": "Person"}}
    back = ad.sdk_round_trip(hand)
    assert back.request.intent.slots["Person"].value == "Papa"


@requires_sdk
def test_response_envelope_with_elicit_directive_matches_the_sdk():
    d = ad.make_elicit_directive("WhatsMyPlanIntent", {"Person": None}, "Person")
    hand = ad.build_response_envelope("Who is this about?", reprompt="Who?",
                                      directives=[d], should_end_session=False)
    assert hand == ad.build_response_envelope_sdk("Who is this about?", reprompt="Who?",
                                                  directives=[d], should_end_session=False)
    assert hand["response"]["directives"][0]["type"] == "Dialog.ElicitSlot"


@requires_sdk
def test_response_envelope_with_confirm_directive_matches_the_sdk():
    d = ad.make_confirm_directive("RemoveInstructionIntent", {"ItemPosition": "first"})
    hand = ad.build_response_envelope("Just to check?", directives=[d])
    assert hand == ad.build_response_envelope_sdk("Just to check?", directives=[d])
    read_back = ad.sdk_round_trip_response(hand)
    assert read_back.response.directives[0].updated_intent.name == "RemoveInstructionIntent"


@requires_sdk
def test_the_wire_json_uses_no_python_sdk_attribute_names():
    # ask_sdk_model's .to_dict() is snake_case; the Alexa wire is not. If a snake_case key ever
    # leaks into an envelope, a device would read that field as absent.
    envelopes = [
        ad.build_launch_envelope("r-1", "2026-01-01T00:00:00Z"),
        ad.build_intent_envelope("r-2", "2026-01-01T00:00:00Z", "TodaysPlanIntent",
                                 {"Person": "Mum"}),
        ad.build_response_envelope("Nothing due now.", reprompt="Anything else?"),
    ]
    for env in envelopes:
        blob = json.dumps(env)
        assert not re.search(r'"[a-z]+_[a-z]+[A-Za-z]*"\s*:', blob), blob


def test_driver_runs_without_the_sdk_present():
    # The plain-dict path is the shipped path: no import of ask_sdk_model is required to
    # build an envelope, so the driver works on a machine with the package absent.
    env = ad.build_response_envelope("For Papa: the first item.", should_end_session=True)
    assert env["response"]["shouldEndSession"] is True
    assert env["response"]["outputSpeech"]["ssml"].startswith("<speak>")


# ================================================================ SSML + escaping
def test_ssml_escapes_markup_and_ampersands():
    escaped = ad.wrap_ssml("Tom & Jerry's <plan>")
    assert "&amp;" in escaped and "&lt;plan&gt;" in escaped
    ok, _ = ad.check_ssml("Tom & Jerry's <plan>")
    assert ok is True
    # a stray '<' that is not XML-escaped would not parse
    assert ad.check_ssml("bad <tag")[0] is True   # we escape, so it parses


def test_ssml_root_is_speak():
    ok, detail = ad.check_ssml("anything")
    assert ok and "speak" in detail


# ================================================================ directive selection
def test_elicitation_selects_elicit_slot_with_the_right_slot():
    structured = {"needs_clarification": True, "question": "Who is this about?"}
    directives = ad.choose_directives("WhatsMyPlanIntent", {"Person": "Uncle"}, structured)
    assert [d["type"] for d in directives] == ["Dialog.ElicitSlot"]
    assert directives[0]["slotToElicit"] == "Person"


def test_confirmation_selects_confirm_intent():
    structured = {"needs_confirmation": True, "question": "Really delete?"}
    directives = ad.choose_directives("RemoveInstructionIntent",
                                      {"ItemPosition": "first"}, structured)
    assert [d["type"] for d in directives] == ["Dialog.ConfirmIntent"]


def test_plain_answer_emits_no_directive():
    structured = {"items": [{"ref": "i-abc", "text": "rest"}], "person": "Papa"}
    assert ad.choose_directives("WhatsMyPlanIntent", {"Person": "Papa"}, structured) == []


def test_never_invents_a_directive_beyond_the_three_allowed():
    for structured in ({"needs_clarification": True}, {"needs_confirmation": True}, {"items": []}):
        for d in ad.choose_directives("WhatsMyPlanIntent", {}, structured):
            assert d["type"] in {"Dialog.ElicitSlot", "Dialog.ConfirmIntent",
                                        "Dialog.Delegate"}


# ================================================================ safety checks can fail
def test_dose_leak_check_fires_on_a_planted_amount():
    # PROOF the check is not vacuous: a raw dose in the envelope is caught (and only the
    # field path is named, never the value).
    planted = {"response": {"output_speech": {"ssml": "<speak>Take 500 mg iron</speak>"}}}
    ok, detail = ad.check_dose_free(planted)
    assert ok is False
    assert "ssml" in detail and "500 mg" not in detail      # path reported, value not echoed


def test_dose_leak_check_passes_a_masked_answer():
    env = ad.build_response_envelope("Take your prescribed amount iron after dinner.")
    assert ad.check_dose_free(env)[0] is True


def test_word_cap_uses_the_repos_own_limit():
    # the device's ceiling is the number the SHIPPED artifact declares, not one invented here, and a
    # style may only ever tighten it. The service's own spoken answer is held to a stricter 45 words
    # (tests/test_guards.py:347), so a plan answer can never reach this ceiling at all.
    assert ad.WORD_CAP == ad.MODEL["answerGuidance"]["maxSpokenWords"]
    assert ad.WORD_CAP == 48
    assert ad.check_word_cap(" ".join(["word"] * 48))[0] is True
    assert ad.check_word_cap(" ".join(["word"] * 49))[0] is False
    for style in ad.DELIVERY["styles"].values():
        assert style["maxSpokenWords"] <= ad.WORD_CAP, style


def test_trace_never_records_a_raw_dose():
    scrubbed = ad._scrub({"text": "Take 500 mg paracetamol", "n": ["give two tablets"]})
    assert "500 mg" not in json.dumps(scrubbed)
    assert ad.MASK_TOKEN in json.dumps(scrubbed)


def test_grounded_quote_accepts_only_verbatim_spans():
    transcript = "Doctor: Take 200 mg iron after dinner."
    ok, _ = ad.check_grounded("Take your prescribed amount iron after dinner", transcript)
    assert ok is True                                  # masked span traces back verbatim
    bad, _ = ad.check_grounded("Take your prescribed amount aspirin at dawn", transcript)
    assert bad is False                                # invented words are rejected


# ================================================================ CLI surface (no network)
def test_list_scenarios_exits_zero_without_touching_a_server():
    assert ad.main(["--list"]) == 0


def test_unknown_scenario_is_rejected():
    assert ad.main(["--scenario", "does-not-exist"]) == 2


# ================================================================ the dialog the artifact declares
def test_a_required_slot_the_customer_never_named_is_asked_not_guessed():
    intent, args = ad.route_intent("remove that instruction", ["Papa", "Mother"])
    assert intent == "RemoveInstructionIntent"
    assert args == {}                                    # nothing said which item
    step = ad.next_dialog_step(intent, ad.Driver._slots_from_args(intent, args))
    assert step == {"stage": "elicit", "slot": "ItemPosition", "prompt": "ElicitItemPrompt"}


def test_a_spoken_value_resolves_to_the_one_the_contract_names():
    assert ad.fill_slot("RemoveInstructionIntent", "ItemPosition", "the second one", []) == "second"
    assert ad.fill_slot("WhatsMyPlanIntent", "Person", "it is for mum", ["Mother"]) == "mum"
    assert ad.fill_slot("WhatsMyPlanIntent", "Section", "the medicine ones", []) == "medication"
    # an answer that names nothing in the model's list stays a miss: the device asks again
    assert ad.fill_slot("RemoveInstructionIntent", "ItemPosition", "the purple one", []) is None


def test_the_note_slot_keeps_the_sentence_exactly_as_said():
    assert ad.fill_slot("AddVerbalNoteIntent", "NoteText",
                        "call the clinic on tuesday", []) == "call the clinic on tuesday"


def test_a_confirmation_lands_on_the_argument_the_model_names():
    assert ad.confirm_argument("RemoveInstructionIntent") == "confirm"
    assert ad.confirm_argument("MarkDoneIntent") is None      # no yes is required there
    assert ad.confirm_argument("WhatsMyPlanIntent") is None


def test_a_prompt_is_the_catalog_line_with_nothing_invented():
    assert ad.render_prompt("ElicitItemPrompt", {}) == ad.PROMPTS["ElicitItemPrompt"]
    assert "second" in ad.render_prompt("ConfirmRemovePrompt", {"ItemPosition": "second"})
    # a placeholder nothing was said for keeps its marker rather than becoming a guess
    assert "{ItemPosition}" in ad.render_prompt("ConfirmRemovePrompt", {})


def test_an_elicit_turn_is_built_without_a_request_ever_reaching_the_server():
    # The url is unreachable on purpose: an elicit turn posts nothing, so it cannot fail.
    driver = ad.Driver("http://127.0.0.1:1/mcp")
    turn, chain = driver._run_phrase(1, "mark it done", "")
    assert chain["intent"] == "MarkDoneIntent" and chain["step"]["slot"] == "ItemPosition"
    assert turn["mcp"]["tool"] is None and turn["ms"] == 0
    kinds = [d["type"] for d in turn["_directives"]]
    assert kinds == ["Dialog.ElicitSlot"] and turn["_directives"][0]["slotToElicit"] == "ItemPosition"
    assert turn["spoken_text"] == ad.PROMPTS["ElicitItemPrompt"]
    assert turn["request_envelope"]["request"]["dialogState"] == "IN_PROGRESS"
    assert turn["dialog"] == {"stage": "elicit", "slot": "ItemPosition",
                              "promptId": "ElicitItemPrompt"}
    assert [c["ok"] for c in turn["checks"]] == [True] * len(turn["checks"]), turn["checks"]


def test_a_clear_no_keeps_the_item_and_calls_nothing():
    driver = ad.Driver("http://127.0.0.1:1/mcp")
    chain = {"intent": "RemoveInstructionIntent", "args": {"ref": "second"},
             "step": {"stage": "confirm", "slot": None, "prompt": "ConfirmRemovePrompt"}}
    refused, rest = driver._run_phrase(2, "no, keep it", "", chain)
    assert rest is None and refused["mcp"]["tool"] is None
    assert refused["spoken_text"] == ad.PROMPTS["KeptPrompt"]
    assert refused["_directives"] == [] and refused["dialog"]["stage"] == "refused"
    # anything that is neither a yes nor a no asks again, with the confirmation still open
    again, still = driver._run_phrase(3, "maybe later", "", chain)
    assert still is chain
    assert [d["type"] for d in again["_directives"]] == ["Dialog.ConfirmIntent"]
    assert "second" in again["spoken_text"]


# ================================================================ end-to-end (real server)
def test_golden_plan_end_to_end_against_the_voice_server(voice_server, tmp_path):
    trace = ad.run_scenario("golden-plan", voice_server + "/mcp")
    # schema completeness
    assert set(trace) >= TRACE_KEYS
    assert trace["kind"] == "careloop-alexa-device-trace" and trace["version"] == 1
    assert set(trace["summary"]) == SUMMARY_KEYS
    # 'what is next' is not the intent the artifact names for a reminder, so nothing arms here
    assert trace["summary"]["reminders_armed"] == 0
    for turn in trace["turns"]:
        assert set(turn) >= TURN_KEYS
    # every check passed and, critically, zero dose leaks survived the gate
    assert trace["summary"]["dose_leaks"] == 0
    assert trace["summary"]["checks_failed"] == 0, trace["checks"]
    # no value anywhere in the trace carries a medicine quantity (independent detector)
    assert ad.pp.dose_leaks(trace) == [], ad.pp.dose_leaks(trace)
    # the trace physically lands under data/ (gitignored), never the repo tree
    path = ad.write_trace(trace, str(tmp_path))
    assert os.path.isfile(path) and path.endswith("golden-plan.trace.json")


def test_elicitation_end_to_end_holds_and_refills(voice_server):
    """The ask-back path: an unresolvable person makes the server ask, the driver emits an
    ElicitSlot directive, then re-runs the SAME tool for the picked person."""
    trace = ad.run_scenario("elicitation", voice_server + "/mcp")
    kinds = [[d["type"] for d in
              (t["response_envelope"]["response"].get("directives") or [])]
             for t in trace["turns"]]
    assert ["Dialog.ElicitSlot"] in kinds, kinds
    assert trace["summary"]["dose_leaks"] == 0
    assert trace["summary"]["checks_failed"] == 0, trace["checks"]


def test_chain_end_to_end_asks_twice_before_it_writes(voice_server):
    """The full delegated dialog: elicit the item the customer never named, read the deletion back
    from the server's own words, take a no, ask for the item properly, and only then write. Nothing
    in the middle is a guess, and the six turns carry exactly three calls."""
    trace = ad.run_scenario("chain", voice_server + "/mcp")
    summary = trace["summary"]
    assert summary["checks_failed"] == 0, trace["checks"]
    assert summary["elicits"] == 1 and summary["confirmations_asked"] == 2
    assert summary["confirmations_refused"] == 1
    assert summary["calls"] == 4, [t["mcp"]["tool"] for t in trace["turns"]]
    # an elicit turn asks; it never sends a request
    asked = [t for t in trace["turns"] if (t.get("dialog") or {}).get("stage") == "elicit"]
    assert asked and all(t["mcp"]["tool"] is None for t in asked)
    written = [t["mcp"]["args"] for t in trace["turns"]
               if t["mcp"]["tool"] == "delete_instruction"]
    # the read-back carries no authorisation, the yes does, and no person is ever sent
    assert written == [{"ref": "second"}, {"ref": "third"},
                      {"ref": "third", "confirm": True}], written
    assert trace["summary"]["dose_leaks"] == 0
    assert ad.pp.dose_leaks(trace) == [], ad.pp.dose_leaks(trace)


# ================================================================ the turn nobody spoke
def test_the_artifact_declares_exactly_one_device_initiated_turn():
    """Every field of the reminder is read out of the shipped model, not decided here: a connection
    the platform can actually resume, on an intent that exists, with a prompt that exists."""
    intents = {i["name"] for i in ad.MODEL["languageModel"]["intents"]}
    assert ad.PROACTIVE["intent"] in intents
    assert ad.PROACTIVE["prompt"] in ad.PROMPTS
    assert ad.PROACTIVE["connectionUri"].startswith("connection://")
    # RESUME_SESSION is the only completion that ever produces a resumed turn; the other value would
    # arm a connection that never comes back to be spoken.
    assert ad.PROACTIVE["onCompletion"] == "RESUME_SESSION"
    assert ad.PROACTIVE["requestType"] == "SessionResumedRequest"
    assert ad.PROACTIVE["causeType"] == "ConnectionCompleted"
    assert ad.PROACTIVE["directiveType"] == "Connections.StartConnection"
    assert ad.PROACTIVE["resumeAfterSeconds"] > 0
    assert ad.PROACTIVE["writesAnything"] is False


def test_the_driver_arms_only_a_plain_plan_answer_that_still_has_something_pending():
    assert ad._has_pending({"person": "Papa", "items": [{"ref": "i-1"}]}) is True
    assert ad._has_pending({"person": "Papa", "items": []}) is False
    assert ad._has_pending({"pending": ["take the iron"]}) is True
    # an answer that is only a question has nothing on it to remind anyone about
    assert ad._has_pending({"needs_clarification": True, "question": "Who?"}) is False
    assert ad._has_pending({}) is False


def test_the_start_connection_directive_is_the_artifacts_own():
    d = ad.make_start_connection_directive("careloop-reminder-1")
    assert d == {"type": ad.PROACTIVE["directiveType"], "uri": ad.PROACTIVE["connectionUri"],
                 "onCompletion": ad.PROACTIVE["onCompletion"], "token": "careloop-reminder-1"}


def test_the_resumed_envelope_carries_the_token_that_armed_it():
    hand = ad.build_session_resumed_envelope("r-9", "2026-01-01T00:00:00Z", "careloop-reminder-1")
    request = hand["request"]
    assert request["type"] == ad.PROACTIVE["requestType"]
    assert request["cause"] == {"type": ad.PROACTIVE["causeType"], "token": "careloop-reminder-1",
                               "status": {"code": "SUCCESS"}}
    # nobody said anything: there is no intent and no slot anywhere in the request
    assert "intent" not in request and "dialogState" not in request


@requires_sdk
def test_the_resumed_envelope_matches_the_sdk_wire_json():
    ts = "2026-01-01T00:00:00Z"
    hand = ad.build_session_resumed_envelope("r-9", ts, "careloop-reminder-1")
    assert hand == ad.build_session_resumed_envelope_sdk("r-9", ts, "careloop-reminder-1")
    back = ad.sdk_round_trip(hand)
    assert back.request.object_type == ad.PROACTIVE["requestType"]
    assert back.request.cause.object_type == ad.PROACTIVE["causeType"]
    assert back.request.cause.token == "careloop-reminder-1"
    assert back.request.cause.status.code == "SUCCESS"


@requires_sdk
def test_the_connection_directive_matches_the_sdk_wire_json():
    directive = ad.make_start_connection_directive("t-1")
    hand = ad.build_response_envelope("CareLoop is reading today’s plan back to you.",
                                      reprompt="Help?", directives=[directive],
                                      should_end_session=False)
    assert hand == ad.build_response_envelope_sdk("CareLoop is reading today’s plan back to you.",
                                                  reprompt="Help?", directives=[directive],
                                                  should_end_session=False)
    got = ad.sdk_round_trip_response(hand).response.directives[0]
    assert got.object_type == ad.PROACTIVE["directiveType"]
    assert got.uri == ad.PROACTIVE["connectionUri"]
    assert got.token == "t-1"
    # the SDK models the completion behaviour as an enum; the wire carries its string
    assert got.on_completion.value == ad.PROACTIVE["onCompletion"]


def _reminder_trace(voice_server):
    return ad.run_scenario("reminder", voice_server + "/mcp")


def test_a_reminder_arms_and_the_device_comes_back_on_its_own(voice_server):
    """End to end on live data: the plan answer holds a connection, the session stays open to be
    resumed, and the turn that follows was started by the device — same read-only call, same
    arguments, the artifact's line in front of the server's answer and nothing new written."""
    trace = _reminder_trace(voice_server)
    assert trace["summary"]["checks_failed"] == 0, trace["checks"]
    assert trace["summary"]["dose_leaks"] == 0
    assert trace["summary"]["reminders_armed"] == 1
    assert trace["summary"]["reminders"] == 1
    armed, resumed = trace["turns"][1], trace["turns"][2]

    kinds = [d["type"] for d in (armed["response_envelope"]["response"].get("directives") or [])]
    assert kinds == [ad.PROACTIVE["directiveType"]]
    assert armed["connection"]["uri"] == ad.PROACTIVE["connectionUri"]
    assert armed["connection"]["afterSeconds"] == ad.PROACTIVE["resumeAfterSeconds"]
    assert armed["response_envelope"]["response"]["shouldEndSession"] is False

    assert resumed["state_out"] == "REMINDER" and resumed["proactive"] is True
    # the device waited the interval the artifact declares — a reminder that came early is not
    # the reminder that was asked for
    assert resumed["at_ms"] - armed["at_ms"] == ad.PROACTIVE["resumeAfterSeconds"] * 1000
    # the same question the customer already asked, not a new one
    assert resumed["mcp"] == armed["mcp"]
    assert resumed["spoken_text"].startswith(ad.PROMPTS[ad.PROACTIVE["prompt"]])
    assert "{ItemPosition}" not in resumed["spoken_text"]      # no unfilled placeholder spoken
    # and it stops: the turn the device started arms nothing further
    assert resumed["connection"] is None
    assert ad.pp.dose_leaks(trace) == [], ad.pp.dose_leaks(trace)


def test_the_real_reminder_scenario_converts_and_compares_clean(voice_server):
    """The resumed turn is a comparable row too: keyed on the label both devices give a turn nobody
    spoke, held to the same tool, the same arguments and the same sentence."""
    headless = _reminder_trace(voice_server)
    rows, here, there, problems = ad.compare_traces(headless, _as_browser_trace(headless))
    assert here == there == 2, [r["phrase"] for r in rows if r["field"] == "turn"]
    assert problems == 0, [r for r in rows if not r["ok"]]
    assert "the device resumed its own session" in [r["phrase"] for r in rows
                                                    if r["field"] == "intent"], rows


# ================================================================ cross-device compare
def _turn(index, utterance, intent, tool, args, dialog, spoken, called, delivery=None,
          meta=None, refused=None):
    """One turn in the shape the headless driver writes."""
    return {
        "index": index, "utterance": utterance, "dialog": dialog, "called": called,
        "delivery": delivery, "meta": meta, "refused": refused,
        "request_envelope": {"version": "1.0", "request": {
            "type": "IntentRequest", "intent": {"name": intent}}},
        "mcp": {"tool": tool, "args": args},
        "response_envelope": {}, "spoken_text": spoken, "masked_field_paths": [],
    }


def _headless_trace(turns, summary):
    launch = _turn(0, "(launch)", None, None, {}, None, "CareLoop is ready.", False)
    launch["request_envelope"] = {"version": "1.0",
                                  "request": {"type": "LaunchRequest", "intent": None}}
    all_turns = [launch] + turns
    for key, value in (("delivery_turns", sum(1 for t in all_turns if t.get("delivery"))),
                       ("speaker_turns", sum(1 for t in all_turns
                                             if t["request_envelope"]["request"].get("intent")
                                             and t["request_envelope"]["request"]["intent"]["name"]
                                             == ad.VOICE_PROFILE["intent"])),
                       ("claims_sent", sum(1 for t in all_turns if t.get("meta"))),
                       ("refusals", sum(1 for t in all_turns if t.get("refused")))):
        summary.setdefault(key, value)
    return {"kind": ad.TRACE_KIND, "version": ad.TRACE_VERSION, "scenario": "chain",
            "turns": all_turns,
            "summary": summary, "checks": []}


def _as_browser_trace(headless):
    """Re-emit a headless trace with the field names the browser Device Lab writes, which is
    exactly the mapping --compare depends on: the Lab records a turn that sent nothing as
    `called: false` beside a router row that still names the tool it would have used.

    A device-initiated turn is emitted too, because the browser writes one: its envelope is a
    SessionResumedRequest with no intent in it, and that is the row the reminder is compared on."""
    turns = []
    for t in headless["turns"]:
        request = t["request_envelope"]["request"]
        if request.get("type") not in ("IntentRequest", ad.PROACTIVE["requestType"]):
            continue
        turns.append({"index": t["index"], "utterance": t["utterance"],
                      "routed": {"tool": t["mcp"]["tool"], "args": t["mcp"]["args"]},
                      "called": t["called"], "dialog": t["dialog"],
                      # the Lab writes these three per turn and counts its summaries off them
                      "delivery": t.get("delivery"), "meta": t.get("meta"),
                      "refused": t.get("refused"),
                      "request_envelope": t["request_envelope"], "spoken_text": t["spoken_text"]})
    summary = {key: headless["summary"][key] for key in ad.COMPARE_SUMMARY
               if key in headless["summary"]}
    # The four the two features add are counted the way the browser counts them, off the turn rows
    # just built: a spoken style is one that reached an answer, a claim is one that rode a request,
    # a refusal is the service's. Re-deriving them here is the point — a fixture that hand-wrote them
    # could drift from its own turns and the diff would still pass.
    summary["delivery_turns"] = sum(1 for t in turns if t["delivery"])
    summary["speaker_turns"] = sum(1 for t in turns
                                   if (t["request_envelope"]["request"].get("intent") or {}).get(
                                       "name") == ad.VOICE_PROFILE["intent"])
    summary["claims_sent"] = sum(1 for t in turns if t["meta"])
    summary["refusals"] = sum(1 for t in turns if t["refused"])
    return {"kind": ad.TRACE_KIND, "version": ad.TRACE_VERSION,
            "scenario": headless["scenario"], "turns": turns, "summary": summary}


_ELICIT = {"stage": "elicit", "slot": "ItemPosition", "promptId": "ElicitItemPrompt"}


def _two_device_pair():
    turns = [
        _turn(1, "remove that instruction", "RemoveInstructionIntent", None, {}, _ELICIT,
              "Which item on the plan?", False),
        _turn(2, "the second one", "RemoveInstructionIntent", "delete_instruction",
              {"ref": "second"}, {"stage": "confirm", "slot": None,
                                  "promptId": "ConfirmRemovePrompt"},
              "Just to check - delete the iron item?", True),
        _turn(3, "yes", "RemoveInstructionIntent", "delete_instruction",
              {"ref": "second", "confirm": True}, None, "It has been removed from the plan.",
              True),
    ]
    summary = {"turns": 4, "calls": 2, "elicits": 1, "confirmations_asked": 1,
               "confirmations_refused": 0,
               # a plan-removal conversation arms no reminder: nothing here read a plan back
               "reminders_armed": 0, "reminders": 0}
    return _headless_trace(turns, summary)


def test_one_devices_conversation_equals_the_others_turn_by_turn():
    headless = _two_device_pair()
    rows, here, there, problems = ad.compare_traces(headless, _as_browser_trace(headless))
    assert (here, there) == (3, 3), [r for r in rows if not r["ok"]]
    assert problems == 0, [r for r in rows if not r["ok"]]
    assert all(r["ok"] for r in rows)
    # the launch turn is not a conversation, so it is not compared
    assert [r["phrase"] for r in rows if r["field"] == "intent"] == [
        "remove that instruction", "the second one", "yes"]


def test_a_device_that_held_the_dialog_differently_is_reported_not_absorbed():
    headless = _two_device_pair()
    browser = _as_browser_trace(headless)
    # the browser re-asked instead of eliciting the slot: the same words, a different stage
    browser["turns"][0]["dialog"] = None
    browser["turns"][0]["called"] = True
    browser["summary"]["elicits"] = 0
    rows, _here, _there, problems = ad.compare_traces(headless, browser)
    bad = [(r["turn"], r["field"]) for r in rows if not r["ok"]]
    assert problems == len(bad) and bad, bad
    assert {f for t, f in bad if t == 0} >= {"called", "stage", "prompt_id"}, bad
    assert ("summary.elicits", ) and any(r["field"] == "summary.elicits" and not r["ok"]
                                        for r in rows), bad


def test_a_turn_one_device_had_and_the_other_never_said_diverges(  ):
    headless = _two_device_pair()
    browser = _as_browser_trace(headless)
    browser["turns"].pop()
    rows, here, there, problems = ad.compare_traces(headless, browser)
    assert (here, there) == (3, 2)
    assert any(r["field"] == "turn" and not r["ok"] for r in rows), rows
    assert problems >= 1


def test_the_real_chain_scenario_converts_and_compares_clean(voice_server):
    """End to end on live data: the headless driver's own trace, re-expressed in the browser's
    field names, must diff to zero against itself - including the counters and every spoken line.
    The browser device is checked against the same function in the built page (see the runbook)."""
    headless = ad.run_scenario("chain", voice_server + "/mcp")
    rows, here, there, problems = ad.compare_traces(headless, _as_browser_trace(headless))
    assert here == there == 6, [r["phrase"] for r in rows if r["field"] == "intent"]
    assert problems == 0, [r for r in rows if not r["ok"]]
