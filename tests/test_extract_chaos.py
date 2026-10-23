"""The model path and its degradation ladder (FR-E5, N19): the verifier is a second pass,
not a gate that silently deletes care steps. A fake Bedrock client keeps this hermetic —
no AWS credentials, no boto3.
"""
import json

import pytest

from careloop import extract
from careloop.config import Settings

TRANSCRIPT = ("Doctor: Take 500 mg iron after dinner every day. "
              "Doctor: Avoid tea with meals because it blocks the iron. "
              "Doctor: Come back after six weeks.")


class FakeBedrock:
    """Answers the two prompts the real client is given, from verbatim spans of TRANSCRIPT."""

    def __init__(self, pointers, verdict):
        self.pointers, self.verdict = pointers, verdict
        self.calls = []

    def converse(self, modelId, messages, inferenceConfig):
        text = messages[0]["content"][0]["text"]
        self.calls.append(text)
        body = json.dumps(self.verdict if "verifier" in text[:60] else self.pointers)
        return {"output": {"message": {"content": [{"text": body}]}}, "usage": {}}


@pytest.fixture()
def model_settings(tmp_path):
    s = Settings(env={
        "CARELOOP_DB_ROOT": str(tmp_path),
        "CARELOOP_BEDROCK_MODEL": "us.anthropic.fake",
        "CARELOOP_AUTH": "off",
    })
    assert s.bedrock_model, "the model path is what these tests are about"
    return s


def two_pointers():
    """One span the rules engine finds too (medication) and one it does not (avoid)."""
    sents = extract.sentences(TRANSCRIPT)
    out = []
    for kind, needle in (("medication", "Take 500"), ("avoid", "Avoid tea")):
        s = next(x for x in sents if needle in x["text"])
        out.append({"sid": s["sid"], "span": [s["body_start"], s["body_end"]], "kind": kind,
                    "schedule": "DAILY", "person_ref": None, "confidence": 0.9})
    return out


def one_pointer():
    """A pointer at the second sentence — verbatim, so the quote guard lets it through."""
    return two_pointers()[1:]


def use_model(monkeypatch, pointers, verdict):
    client = FakeBedrock(pointers, verdict)
    monkeypatch.setattr(extract, "_client", lambda: client)
    return client


def test_the_model_path_reaches_the_store_with_its_verdict(model_settings, monkeypatch):
    client = use_model(monkeypatch, two_pointers(), {"supported": True, "reason": "ok"})
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["engine"] == "bedrock+rules" and res["model"] == "us.anthropic.fake"
    assert any("verifier" in c for c in client.calls), "the second pass must have run"
    engines = {i["kind"]: i["engine"] for i in res["items"]}
    assert engines == {"medication": "rules+llm", "avoid": "llm", "appointment": "rules"}
    flagged = {i["kind"]: i["needs_confirm"] for i in res["items"]}
    assert flagged == {"medication": False, "avoid": True, "appointment": False}, \
        "a model-only finding waits for a human; one the rules agree with does not"
    assert not [n for n in res["notes"] if "unavailable" in n]


def test_a_supported_false_verdict_drops_the_item(model_settings, monkeypatch):
    """The one thing the verifier IS allowed to do: remove what it proved unsupported."""
    use_model(monkeypatch, one_pointer(), {"supported": False, "reason": "not-grounded"})
    monkeypatch.setattr(extract, "rules_extract", lambda sents: [])   # nothing corroborates it
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["items"] == [] and res["engine"] == "bedrock+rules"


def test_verify_down_keeps_the_items_and_flags_them(model_settings, monkeypatch):
    """The verifier is the fault: extraction still stands, and what it cannot vouch for is
    flagged for a human instead of quietly dropped."""
    use_model(monkeypatch, one_pointer(), {"supported": True, "reason": "ok"})
    monkeypatch.setattr(extract, "rules_extract", lambda sents: [])   # model-only plan
    model_settings.chaos = {"verify_down"}
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["engine"] == "bedrock+rules", res["notes"]
    assert [i["needs_confirm"] for i in res["items"]] == [True]
    assert any("verifier unavailable" in n for n in res["notes"])
    assert extract.quote_guard(res["items"][0]["text"], extract.sentences(TRANSCRIPT), TRANSCRIPT)


def test_without_the_flag_the_same_item_is_not_flagged(model_settings, monkeypatch):
    use_model(monkeypatch, one_pointer(), {"supported": True, "reason": "ok"})
    monkeypatch.setattr(extract, "rules_extract", lambda sents: [])
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert [i["needs_confirm"] for i in res["items"]] == [False]


def test_bedrock_throttle_falls_back_to_rules_without_erroring(model_settings, monkeypatch):
    model_settings.chaos = {"bedrock_throttle"}
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["engine"] == "rules"
    assert any("rules fallback" in n for n in res["notes"])
    assert res["items"], "the deterministic engine must still produce a plan"


def test_a_model_that_raises_is_degraded_not_fatal(model_settings, monkeypatch):
    def boom():
        raise RuntimeError("no credentials")
    monkeypatch.setattr(extract, "_client", boom)
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["engine"] == "rules" and res["items"]
    assert any("model error" in n for n in res["notes"])


def test_chaos_only_touches_the_call_site_it_names(model_settings, monkeypatch):
    use_model(monkeypatch, one_pointer(), {"supported": True, "reason": "ok"})
    model_settings.chaos = {"db_locked"}          # a store fault, not a model one
    res = extract.process_visit(TRANSCRIPT, model_settings)
    assert res["engine"] == "bedrock+rules"
    assert not [n for n in res["notes"] if "unavailable" in n or "error" in n]


def test_no_chaos_flag_means_the_model_path_is_untouched(model_settings):
    assert model_settings.chaos == set()
    assert extract._chaos(model_settings, "verify_down") is None
