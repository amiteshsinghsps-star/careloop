"""The shipped voice interaction model and the shipped tool contracts must describe the same calls.

`alexa/interaction-model.json` and `artifacts/contracts/*.schema.json` are written by different code
in the same run, so nothing else notices when one moves. These tests judge the committed files against
each other, and then re-create the two mappings that were actually wrong once (a person sent to
`mark_done` as `person`, a person sent to `delete_instruction` at all) to prove the gate still fails
on them.
"""
import copy
import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts import make_addon                                       # noqa: E402
from scripts.make_addon import routing_findings                      # noqa: E402

MODEL_PATH = os.path.join(ROOT, "alexa", "interaction-model.json")
CONTRACTS = sorted(glob.glob(os.path.join(ROOT, "artifacts", "contracts", "*.schema.json")))


@pytest.fixture(scope="module")
def model():
    with open(MODEL_PATH, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def docs():
    """The published contracts, read as the artifacts a reviewer would read — not from Python."""
    tools = []
    for path in CONTRACTS:
        with open(path, encoding="utf-8") as handle:
            tools.append(json.load(handle))
    assert tools, "no contract artifacts to check the model against"
    return {"tools": tools}


def intent(model, name):
    return next(i for i in model["languageModel"]["intents"] if i["name"] == name)


def test_committed_model_routes_cleanly(model, docs):
    assert routing_findings(model, docs) == []


def test_every_voice_intent_declares_its_slots(model):
    """Dialog delegation needs the slot list in the artifact, not only in the generator."""
    for entry in model["languageModel"]["intents"]:
        assert "slots" in entry, "%s ships no slot list" % entry["name"]
        for slot in entry["slots"]:
            assert set(slot) >= {"name", "type", "required"}


def test_a_person_is_never_sent_to_a_tool_that_has_no_person_argument(model, docs):
    """The real defect: `mark_done` names the person `who`, and `delete_instruction` has none."""
    broken = copy.deepcopy(model)
    intent(broken, "MarkDoneIntent")["routing"]["slotArguments"]["Person"] = "person"
    findings = routing_findings(broken, docs)
    assert any("mark_done does not accept" in p for p in findings), findings

    broken = copy.deepcopy(model)
    intent(broken, "RemoveInstructionIntent")["routing"]["slotArguments"]["Person"] = "person"
    findings = routing_findings(broken, docs)
    assert any("delete_instruction does not accept" in p for p in findings), findings


def test_an_unmapped_person_stays_speaking_only(model):
    """A slot collected for the read-back but not sent is deliberate, and stays that way."""
    mapping = intent(model, "RemoveInstructionIntent")["routing"]["slotArguments"]
    assert "Person" not in mapping
    assert any(s["name"] == "Person" for s in intent(model, "RemoveInstructionIntent")["slots"])


def test_a_required_tool_argument_must_be_fillable_by_a_required_slot(model, docs):
    """Drop `required` from the item slot and the call could always go out without a `ref`."""
    broken = copy.deepcopy(model)
    for slot in intent(broken, "MarkDoneIntent")["slots"]:
        if slot["name"] == "ItemPosition":
            slot["required"] = False
    findings = routing_findings(broken, docs)
    assert any("requires 'ref'" in p for p in findings), findings


def test_a_synonym_the_tools_enum_would_reject_is_reported(model, docs):
    """The section type resolves to the server's kind; reverting that sends 'tablet' as `kind`."""
    broken = copy.deepcopy(model)
    for slot_type in broken["languageModel"]["types"]:
        if slot_type["name"] == "CareLoopPlanSection":
            for value in slot_type["values"]:
                value["id"] = value["name"]["value"]
    findings = routing_findings(broken, docs)
    assert any("not one of the kinds get_care_plan accepts" in p for p in findings), findings


def test_a_slot_no_sample_can_speak_is_reported(model, docs):
    """An elicited slot nothing can fill is a device that asks forever."""
    broken = copy.deepcopy(model)
    entry = intent(broken, "ListPeopleIntent")
    entry["slots"] = [{"name": "Person", "type": "CareLoopPerson", "required": True}]
    findings = routing_findings(broken, docs)
    assert any("ListPeopleIntent: slot Person is declared and would be elicited" in p
               for p in findings), findings


def test_the_yes_no_answer_lands_on_an_argument_the_tool_has(model, docs):
    entry = intent(model, "RemoveInstructionIntent")
    assert entry["routing"]["confirmArgument"] == "confirm"
    broken = copy.deepcopy(model)
    intent(broken, "RemoveInstructionIntent")["routing"]["confirmArgument"] = "confirmed"
    findings = routing_findings(broken, docs)
    assert any("delete_instruction does not accept" in p for p in findings), findings


def test_section_synonyms_resolve_onto_the_kinds_the_contract_names(model, docs):
    kinds = {}
    for slot_type in model["languageModel"]["types"]:
        if slot_type["name"] == "CareLoopPlanSection":
            kinds = {v["name"]["value"]: v["id"] for v in slot_type["values"]}
    schema = next(t for t in docs["tools"] if t["name"] == "get_care_plan")["inputSchema"]
    assert set(kinds.values()) <= set(schema["properties"]["kind"]["enum"])
    assert len(kinds) == 14
