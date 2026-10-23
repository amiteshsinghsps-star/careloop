"""tools — the MCP tool contract (Part D.1): one intent per tool, validated inputs,
declared outputSchemas, stable ids, plain-language errors with a next step.

Every handler returns `structuredContent` only. The server adds `content[].text`
generated from the same data, and everything on the Alexa surface passes through
`policy_guard.guard_payload` (dose masking + disclaimer + attribution).
"""
import hashlib
import json
import re

from . import authz, policy_guard as pg, store, timecraft
from .config import KINDS, sanitize_person

ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
_ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
              "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "one": 1, "two": 2, "three": 3}


def _ordinal_label(i):
    return ORDINALS.get(i, f"item {i}")


class ToolError(Exception):
    def __init__(self, message, next_step=None):
        super().__init__(message)
        self.message = message
        self.next_step = next_step or "Ask for the care plan list to see what is available."


def resolve_ref(cell, ctx, ref):
    """Stable ids plus ordinal words ("the second one") resolved from session context."""
    ref = (ref or "").strip()
    m = re.match(r"^i-[0-9a-f]{6,12}$", ref)
    if m:
        return cell.get_item(ref)
    n = _ORD_WORDS.get(ref.lower())
    if n and ctx.get("last_refs") and n <= len(ctx["last_refs"]):
        return cell.get_item(ctx["last_refs"][n - 1])
    for p, refs in (ctx.get("last_refs_by_person") or {}).items():
        if n and n <= len(refs):
            return cell.get_item(refs[n - 1])
    return None


def _person_or_default(cell, ctx, args, allow_clarify=True):
    person = sanitize_person(args.get("person"))
    if person:
        resolved = cell.resolve_person(person)
        if resolved:
            return resolved, None
        people = cell.people()
        if len(people) == 1 and allow_clarify:
            return people[0]["name"], None
        if allow_clarify and people:
            # D11 elicitation-friendly: return a short question so Alexa can ask
            names = ", ".join(p["name"] for p in people[:3])
            return None, {"needs_clarification": True,
                          "question": f"Who is this about? The people I know in this household are {names}."}
        return None, {"error": f"I don't have a person called {person} in this household.",
                      "next_step": "Ask 'who is in my household?' to see the list."}
    if ctx.get("person"):
        return ctx["person"], None
    people = cell.people()
    if len(people) == 1:
        return people[0]["name"], None
    return None, None  # no person scope: whole household


# ------------------------------------------------------------------ tool defs
TOOL_DEFS = [
    {
        "name": "about_careloop",
        "description": ("Explain what CareLoop can do. Use when the customer asks what this "
                        "service can help with, or for a first-run example of phrasing. "
                        "Synonyms: help, capabilities, what can you do."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "capabilities": {"type": "array", "items": {"type": "string"}},
            "first_run_notice": {"type": "string"}, "examples": {"type": "array"},
            "disclaimer": {"type": "string"}}, "required": ["capabilities", "disclaimer"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": True, "action": None,
    },
    {
        "name": "get_care_plan",
        "description": ("List what to do next for one person's care plan: medicines, tasks, "
                        "things to avoid, and follow-ups, with what is pending first. Synonyms: "
                        "what's next, what is the plan, list instructions, what did the doctor say."),
        "inputSchema": {"type": "object", "properties": {
            "person": {"type": "string", "maxLength": 40, "description":
                "Who the plan is about, e.g. mother, father, Papa, name. Synonyms mapped to household members."},
            "kind": {"type": "string", "enum": list(KINDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5}},
            "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "person": {"type": ["string", "null"]},
            "items": {"type": "array"}, "avoid": {"type": "array"},
            "attribution": {"type": "string"}, "disclaimer": {"type": "string"},
            "dose_policy": {"type": "string"}}, "required": ["items", "attribution", "disclaimer"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
        "ui": True,
    },
    {
        "name": "explain_instruction",
        "description": ("Show the clinician's original recorded words for one item from the last "
                        "care plan, and when it was recorded. Synonyms: why, source, proof."),
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "maxLength": 40,
                    "description": "The item from the previous list: its id, or an ordinal like 'second'."}},
            "required": ["ref"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "quote": {"type": "string"}, "recorded_on": {"type": "string"},
            "source": {"type": "string"}, "disclaimer": {"type": "string"}},
            "required": ["quote", "recorded_on", "source"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
    },
    {
        "name": "mark_done",
        "description": ("Record that one care item was completed. Synonyms: finished, taken, did it, done."),
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "maxLength": 40},
            "who": {"type": "string", "maxLength": 30},
            "idempotency_key": {"type": "string", "maxLength": 64,
                                "description": "Reuse the same key when retrying this exact completion."}},
            "required": ["ref"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "ref": {"type": "string"}, "status": {"type": "string"},
            "remaining_count": {"type": "integer"}}, "required": ["ref", "status"]},
        "annotations": {"idempotentHint": True}, "guest_safe": False, "action": "MarkDone",
    },
    {
        "name": "confirm_instruction",
        "description": ("Record that the caregiver confirmed a flagged item with the clinician, "
                        "clearing its confirm-needed flag. Synonyms: confirm, checked with the "
                        "doctor, verified, that one is fine now."),
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "maxLength": 40}}, "required": ["ref"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "ref": {"type": "string"}, "needs_confirm": {"type": "boolean"}}, "required": ["ref"]},
        "annotations": {}, "guest_safe": False, "action": "ConfirmItem",
    },
    {
        "name": "add_note",
        "description": ("Add one short instruction the family was told verbally, so it is remembered "
                        "with the plan. CareLoop stores the words exactly; it never adds medical advice. "
                        "Synonyms: remember that, note this, the nurse said, add a note."),
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "minLength": 3, "maxLength": 300},
            "person": {"type": "string", "maxLength": 40}},
            "required": ["text"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "items": {"type": "array"}, "status": {"type": "string"}}, "required": ["items"]},
        "annotations": {}, "guest_safe": False, "action": "AddNote",
    },
    {
        "name": "delete_instruction",
        "description": ("Remove an item from the care plan. The first call without confirmation only "
                        "reads the item back; the customer must confirm before it is deleted. "
                        "Synonyms: delete, remove, take off the list, cross out."),
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "maxLength": 40},
            "confirm": {"type": "boolean", "description": "Set true only after the customer confirms."}},
            "required": ["ref"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "ref": {"type": "string"}, "deleted": {"type": "boolean"},
            "needs_confirmation": {"type": "boolean"}, "question": {"type": "string"},
            "summary": {"type": "object"}}, "required": ["ref"]},
        "annotations": {"destructiveHint": True}, "guest_safe": False, "action": "DeleteItem",
    },
    {
        "name": "list_people",
        "description": "List the people in this household's care plans. Synonyms: who, family members.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"people": {"type": "array"}},
                         "required": ["people"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
        "ui": True,
    },
    {
        "name": "todays_plan",
        "description": ("Today's agenda for one person, ordered by time of day: morning, after food, "
                        "evening, bedtime. Synonyms: what today, today's schedule."),
        "inputSchema": {"type": "object", "properties": {
            "person": {"type": "string", "maxLength": 40}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"slots": {"type": "array"}},
                         "required": ["slots"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
        "ui": True,
    },
    {
        "name": "what_changed",
        "description": ("Compare this visit with the previous recorded visit: what is new, what "
                        "stopped, what changed. Synonyms: diff, since last time."),
        "inputSchema": {"type": "object", "properties": {
            "person": {"type": "string", "maxLength": 40}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "new": {"type": "array"}, "stopped": {"type": "array"}, "changed": {"type": "array"},
            "compared_to": {"type": "string"}}, "required": ["new", "stopped", "changed"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
    },
    {
        "name": "prepare_next_visit",
        "description": ("Build the caregiver's question list for the next visit: items still flagged "
                        "for confirmation, superseded items, and recent notes. Contains no advice. "
                        "Synonyms: what to ask the doctor, prepare for the appointment, questions to "
                        "take along."),
        "inputSchema": {"type": "object", "properties": {
            "person": {"type": "string", "maxLength": 40}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "open_flags": {"type": "array"}, "skipped": {"type": "array"}, "notes": {"type": "array"}},
            "required": ["open_flags"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
    },
    {
        "name": "daily_digest",
        "description": ("How much of today's plan is done versus pending. Synonyms: progress, how am I doing."),
        "inputSchema": {"type": "object", "properties": {
            "person": {"type": "string", "maxLength": 40}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {
            "done": {"type": "integer"}, "pending": {"type": "integer"},
            "last_done_at": {"type": ["string", "null"]}}, "required": ["done", "pending"]},
        "annotations": {"readOnlyHint": True}, "guest_safe": False, "action": "ViewPlan",
    },
]


# ------------------------------------------------------------------ handlers
def _mask_item(it, full=False):
    text = it["text"]
    sched = it.get("sched_json", {}).get("raw") or ""
    if not full:
        # The raw schedule fragment is a slice of the clinician's sentence, so it can carry a
        # dose: masked here as well as at the outbound gate, never sent to the voice raw.
        text = pg.mask_doses(text)
        sched = pg.mask_doses(sched)
    return {"ref": it["ref"], "kind": it["kind"], "text": text,
            "schedule": sched,
            "status": it["status"], "needs_confirm": it["needs_confirm"]}


def h_about(ctx, cell, args):
    return {"capabilities": [
        "Repeat what your clinician said in a recorded visit, with the original words",
        "List what is next for each person in your household",
        "Explain why an instruction was given and when it was recorded",
        "Mark items done and prepare questions for the next visit",
        "Compare this visit with the previous one"],
        "first_run_notice": pg.GUEST_NOTICE,
        "examples": ["What's next for Papa?", "Why did the clinician say that?",
                     "What changed since the last visit?", "Mark the evening item as done"],
        "disclaimer": pg.DISCLAIMER}


def h_get_plan(ctx, cell, args):
    person, note = _person_or_default(cell, ctx, args)
    if isinstance(note, dict) and note.get("needs_clarification"):
        return note
    if isinstance(note, dict) and note.get("error"):
        raise ToolError(note["error"], note.get("next_step"))
    kinds = [args["kind"]] if args.get("kind") else None
    limit = int(args.get("limit") or 5)
    items = cell.list_items(person=person, kinds=kinds, statuses=("pending",))
    pend = [i for i in items if i["status"] == "pending"]
    pend.sort(key=lambda i: (i["needs_confirm"], timecraft.slot_order(
        (i["sched_json"].get("day_parts") or ["UNKNOWN"])[0])))
    shown = pend[:limit]
    refs = [i["ref"] for i in shown]
    ctx["last_refs"] = refs
    if person:
        ctx.setdefault("last_refs_by_person", {})[person] = refs
    if person:
        cell.set_context(person, refs, ctx["ttl"])
    avoid = [pg.mask_doses(i["text"]) for i in cell.list_items(person=person, kinds=("avoid",),
                                                               statuses=("pending",))][:5]
    import time as _t
    date = _t.strftime("%d %B %Y", _t.localtime(shown[0]["created_at"])) if shown else ""
    out = {"person": person,
           "items": [_mask_item(i) for i in shown],
           "avoid": avoid,
           "attribution": pg.ATTRIBUTION_TEMPLATE.format(date=pg.escape(date) or "your last visit"),
           "disclaimer": pg.DISCLAIMER, "dose_policy": pg.DOSE_POLICY,
           "count_hint": f"Showing the {len(shown)} most immediate of {len(pend)} pending items."}
    if not shown:
        out["items"] = []
        out["empty"] = "Nothing is pending in the plan I have."
    return out


def h_explain(ctx, cell, args):
    it = resolve_ref(cell, ctx, args.get("ref"))
    if not it:
        raise ToolError("I couldn't find that item in the plan you last asked about.",
                        "Ask for the care plan again and refer to it by its position, like the second one.")
    import time as _t
    return {"quote": it["quote"], "recorded_on": _t.strftime("%d %B %Y", _t.localtime(it["created_at"])),
            "source": "clinician" if it["source"] in ("bedrock", "rules", "rules+llm") else it["source"],
            "confidence": it["confidence"], "needs_confirm": it["needs_confirm"],
            "disclaimer": pg.DISCLAIMER}


def h_mark_done(ctx, cell, args):
    it = resolve_ref(cell, ctx, args.get("ref"))
    if not it:
        raise ToolError("I couldn't find that item.", "Ask for the care plan first, then say which one.")
    res = cell.mark_done(it["ref"], who=sanitize_person(args.get("who"), 30),
                         idem_key=args.get("idempotency_key"))
    if res.get("error") == "version-conflict":
        raise ToolError("That item just changed — someone else updated the plan.",
                        "Ask for the care plan again before marking it done.")
    if res.get("error"):
        raise ToolError("I couldn't find that item.", "Ask for the care plan first.")
    remaining = len(cell.list_items(person=it["person"], statuses=("pending",)))
    return {"ref": it["ref"], "status": "done", "remaining_count": remaining,
            "replayed": bool(res.get("replayed"))}


def h_confirm(ctx, cell, args):
    it = resolve_ref(cell, ctx, args.get("ref"))
    if not it:
        raise ToolError("I couldn't find that item.", "Ask for the care plan first.")
    return cell.confirm_item(it["ref"])


def h_add_note(ctx, cell, args):
    text = (args.get("text") or "").strip()[:300]
    if len(text) < 3:
        raise ToolError("The note was too short.", "Say the full sentence you want remembered.")
    if pg.is_crisis(text):
        return {"crisis": True, "items": [], "message": pg.CRISIS_REDIRECT,
                "disclaimer": pg.DISCLAIMER}
    if pg.mentions_child(text):
        return {"items": [], "status": "rejected",
                "message": "CareLoop is for adult care plans and doesn't store information about children."}
    if pg.looks_like_injection(text):
        raise ToolError("That note looks like instructions for a system rather than a care instruction.",
                        "Add the note in the CareLoop companion page if you really need it.")
    # A note may name someone the plan doesn't know yet — families say "the nurse" or a new
    # name first. Resolve aliases, otherwise remember the person; only an ambiguous household
    # (several people, none named) stops to ask.
    person = sanitize_person(args.get("person")) or ctx.get("person")
    if not person:
        people = cell.people()
        if len(people) > 1:
            names = ", ".join(p["name"] for p in people[:3])
            return {"needs_clarification": True, "items": [],
                    "question": f"Who should I remember this for? The people I know are {names}."}
        person = people[0]["name"] if people else "Household"
    person = cell.resolve_person(person) or person
    if not cell.resolve_person(person):
        cell.add_person(person)
    sched = timecraft.parse_schedule(text)
    out, status = cell.add_item(person=person, kind="task", sched_text=text[:60], sched_json=sched,
                                quote=text, qstart=0, qend=len(text), transcript_hash="note",
                                source="caregiver-note", confidence=1.0, needs_confirm=False)
    return {"items": [_mask_item(out)], "status": status, "disclaimer": pg.DISCLAIMER}


def h_delete(ctx, cell, args):
    it = resolve_ref(cell, ctx, args.get("ref"))
    if not it:
        raise ToolError("I couldn't find that item.", "Ask for the care plan first, then say which one.")
    confirm = bool(args.get("confirm"))
    res = cell.delete_item(it["ref"], confirm=confirm)
    if res.get("needs_confirmation"):
        res["summary"]["quote"] = pg.mask_doses(res["summary"]["quote"])
        res["question"] = (f"Just to check — delete the {it['kind']} item for {it['person']}, "
                           f"about \u201c{pg.mask_doses(it['quote'])[:120]}\u201d?")
    if res.get("error"):
        raise ToolError("I couldn't find that item.", "Ask for the care plan first.")
    res["ref"] = it["ref"]
    return res


def h_list_people(ctx, cell, args):
    people = cell.people()
    out = []
    for p in people:
        pend = cell.list_items(person=p["name"], statuses=("pending",))
        out.append({"name": p["name"], "role": p["role"], "pending_count": len(pend)})
    return {"people": out, "disclaimer": pg.DISCLAIMER}


def h_todays(ctx, cell, args):
    person, note = _person_or_default(cell, ctx, args)
    if isinstance(note, dict) and note.get("needs_clarification"):
        return note
    import datetime as _dt
    today = _dt.date.today().isoformat()
    items = cell.list_items(person=person, statuses=("pending",))
    slots = {}
    for it in items:
        occ = timecraft.next_occurrences(it["sched_json"], it["sched_json"].get("raw_anchor") or today, days=1)
        parts = [p for d, p in occ if d == today] or (it["sched_json"].get("day_parts") or ["UNKNOWN"])
        for p in parts:
            slots.setdefault(p, []).append(_mask_item(it))
    ordered = [{"part_of_day": k, "items": v} for k, v in
               sorted(slots.items(), key=lambda kv: timecraft.slot_order(kv[0]))]
    ctx["last_refs"] = [i["ref"] for s in ordered for i in s["items"]]
    return {"person": person, "date": today, "slots": ordered,
            "attribution": "According to your clinician's recorded instructions.",
            "disclaimer": pg.DISCLAIMER, "dose_policy": pg.DOSE_POLICY}


def h_what_changed(ctx, cell, args):
    person, note = _person_or_default(cell, ctx, args, allow_clarify=False)
    if isinstance(note, dict) and note.get("error"):
        raise ToolError(note["error"], note.get("next_step"))
    visits = cell.visits()
    if len(visits) < 2:
        return {"new": [], "stopped": [], "changed": [],
                "message": "I only have one recorded visit, so there is nothing to compare yet."}
    boundary = visits[-2]["created_at"]
    diff = cell.what_changed(boundary)
    import time as _t
    compared = _t.strftime("%d %B", _t.localtime(boundary))
    ctx["last_refs"] = [i["ref"] for i in diff["new"]]
    return {"new": [_mask_item(i) for i in diff["new"]],
            "stopped": [_mask_item(i) for i in diff["stopped"]],
            "changed": [_mask_item(i) for i in diff["changed"]],
            "compared_to": f"the visit recorded on {compared}",
            "attribution": "Both sets are the clinician's own recorded words.",
            "disclaimer": pg.DISCLAIMER}


def h_prepare(ctx, cell, args):
    person, note = _person_or_default(cell, ctx, args, allow_clarify=False)
    if isinstance(note, dict) and note.get("error"):
        raise ToolError(note["error"], note.get("next_step"))
    items = cell.list_items(person=person, statuses=("pending", "superseded"))
    open_flags = [_mask_item(i) for i in items if i["needs_confirm"] and i["status"] == "pending"]
    skipped = [_mask_item(i) for i in items if i["status"] == "superseded"]
    notes = [{"text": pg.mask_doses(i["text"])} for i in items if i["source"] == "caregiver-note"]
    return {"open_flags": open_flags, "skipped": skipped, "notes": notes[-5:],
            "message": "Ask the clinician about anything flagged here at your next visit.",
            "disclaimer": pg.DISCLAIMER}


def h_digest(ctx, cell, args):
    person, note = _person_or_default(cell, ctx, args, allow_clarify=False)
    if isinstance(note, dict) and note.get("error"):
        raise ToolError(note["error"], note.get("next_step"))
    items = cell.list_items(person=person, statuses=("pending", "done"))
    import datetime as _dt
    today = _dt.date.today().isoformat()
    done = [i for i in items if i["status"] == "done"]
    import time as _t
    done_today = [i for i in done if i.get("done_at") and _t.strftime("%Y-%m-%d", _t.localtime(i["done_at"])) == today]
    pending = [i for i in items if i["status"] == "pending"]
    last = max((i["done_at"] for i in done_today), default=None)
    return {"done": len(done_today), "pending": len(pending),
            "last_done_at": _t.strftime("%H:%M", _t.localtime(last)) if last else None,
            "message": "Every step you take counts, at your own pace." if done_today else
                       "Nothing marked done yet today.",
            "disclaimer": pg.DISCLAIMER}


HANDLERS = {
    "about_careloop": h_about, "get_care_plan": h_get_plan, "explain_instruction": h_explain,
    "mark_done": h_mark_done, "confirm_instruction": h_confirm, "add_note": h_add_note,
    "delete_instruction": h_delete, "list_people": h_list_people, "todays_plan": h_todays,
    "what_changed": h_what_changed, "prepare_next_visit": h_prepare, "daily_digest": h_digest,
}

GUEST_SAFE = {d["name"] for d in TOOL_DEFS if d.get("guest_safe")}
UI_TOOLS = {d["name"]: d for d in TOOL_DEFS if d.get("ui")}
CARD_URI = "ui://careloop/card"


def manifest_hash() -> str:
    core = [{"name": d["name"], "description": d["description"], "inputSchema": d["inputSchema"],
             "outputSchema": d["outputSchema"]} for d in TOOL_DEFS]
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def validate_args(defn: dict, args: dict) -> list:
    """Cheap JSON-Schema-style validation of the subset we declare (Functional §13)."""
    errors = []
    props = defn["inputSchema"].get("properties", {})
    for k in (args or {}):
        if k not in props:
            errors.append(f"unknown parameter {k}")
    for k, spec in props.items():
        v = (args or {}).get(k)
        if v is None:
            if k in defn["inputSchema"].get("required", []):
                errors.append(f"missing {k}")
            continue
        t = spec.get("type")
        if t == "string":
            if not isinstance(v, str):
                errors.append(f"{k} must be text")
            elif "maxLength" in spec and len(v) > spec["maxLength"]:
                errors.append(f"{k} too long")
            elif "minLength" in spec and len(v) < spec["minLength"]:
                errors.append(f"{k} too short")
            if "enum" in spec and v not in spec["enum"]:
                errors.append(f"{k} must be one of {spec['enum']}")
        elif t == "integer":
            try:
                iv = int(v)
            except (TypeError, ValueError):
                errors.append(f"{k} must be a number")
                continue
            if "minimum" in spec and iv < spec["minimum"]:
                errors.append(f"{k} too small")
            if "maximum" in spec and iv > spec["maximum"]:
                errors.append(f"{k} too big")
        elif t == "boolean":
            if not isinstance(v, bool):
                errors.append(f"{k} must be true or false")
    if defn["inputSchema"].get("additionalProperties") is False and errors:
        pass
    return errors


def authorize(defn, identity, surface):
    action = defn.get("action")
    if action is None:
        return authz.Decision(True, "public")
    principal = {"hid": identity["hid"], "role": identity.get("role", "viewer"),
                 "role_group": "HouseholdMembers"}
    return authz.decide(principal, action, {"hid": identity["hid"]}, {"surface": surface})
