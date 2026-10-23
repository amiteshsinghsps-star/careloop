"""dataset — the synthetic gold corpus and its integrity checks.

The corpus lives in ``scripts/eval/cases/*.json``.  It is *authored by hand* and never
written back from model output (there is deliberately no ``--update-gold``): the
gold quotes are the reference against which the extractor is judged, so anything
that could silently rebase them would make the numbers meaningless.

Case schema (every field required unless marked optional)::

    id                unique, used by --filter
    title             one short line
    person            who the plan belongs to (an adult; see ADULT_LINT)
    transcript        the invented clinician voice ...  OR  "lines": [str, ...]
    items             [ {kind, quote, person?, schedule?, needs_confirm?, note?} ]
    expected_refusal  optional: "crisis" | "child" | "injection"
    tags              optional: [str, ...]

``schedule`` is the *clinically correct* reading of the timing the clinician gave,
expressed with timecraft's own vocabulary.  ``null`` means the sentence states no usable
timing, so the correct canonical reading is UNKNOWN: an item that invents a time slot is
a schedule error.  ``"absent"`` (the default when an author writes nothing) means the same
thing but is not counted in schedule accuracy, only watched for invented slots.
"""
import glob
import hashlib
import json
import os
import re

from careloop import timecraft
from careloop.config import KINDS

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")

# Corpus contract -- the PRD's evidence requirement, encoded as data.
MIN_CASES = 18
MIN_GOLD_ITEMS = 120
REQUIRED_ADVERSARIAL = ("crisis", "child", "injection", "empty", "self-contradiction",
                        "dose-forms")

REFUSALS = ("crisis", "child", "injection")
SCHEDULE_KINDS = ("RECURRENCE", "ONCE", "WEEKLY", "INTERVAL", "UNKNOWN")
SCHEDULE_PARTS = tuple(timecraft.PARTS) + ("ANYTIME",)

# Nothing in this corpus may describe a child being the patient.  These are ordinary
# geriatric words, so the lint is only about *who the transcript is about*: it is the
# same test the harness runs on the corpus, kept here so the data fails loudly.
ADULT_LINT = re.compile(r"\b(baby|infant|toddler|pediatric|paediatric|newborn|grade [0-9])\b",
                        re.IGNORECASE)


def transcript_of(case: dict) -> str:
    """Cases may be authored as "lines" for readability; the transcript is a string."""
    if case.get("transcript"):
        return case["transcript"]
    return "\n".join(case.get("lines") or [])


def normalize(case: dict) -> dict:
    case = dict(case)
    case["transcript"] = transcript_of(case)
    case.setdefault("tags", [])
    case.setdefault("expected_refusal", None)
    case.setdefault("person", "")
    items = []
    for g in case.get("items", []):
        g = dict(g)
        g.setdefault("person", case["person"])
        g.setdefault("needs_confirm", False)
        g.setdefault("note", "")
        g.setdefault("schedule", "absent")  # "absent" = the gold sentence gives no timing
        items.append(g)
    case["items"] = items
    return case


def load_dataset(cases_dir: str = CASES_DIR) -> dict:
    """Load, normalise and audit the corpus.  Returns {cases, fingerprint, errors}."""
    paths = sorted(glob.glob(os.path.join(cases_dir, "*.json")))
    raw, versions, errors = [], [], []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        versions.append(f"{os.path.basename(p)}@{blob.get('version', 'unversioned')}")
        for c in blob.get("cases", []):
            c = dict(c)
            c["_file"] = os.path.basename(p)
            raw.append(c)

    cases = [normalize(c) for c in raw]
    errors += audit(cases)

    canon = json.dumps([{k: v for k, v in c.items() if k != "_file"} for c in cases],
                       sort_keys=True, ensure_ascii=False)
    gold_items = sum(len(c["items"]) for c in cases)
    return {
        "cases": cases,
        "errors": errors,
        "fingerprint": {
            "version": "+".join(versions) if versions else "empty",
            "sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
            "files": [os.path.basename(p) for p in paths],
            "cases": len(cases),
            "gold_items": gold_items,
            "adversarial": sorted({c["expected_refusal"] or tag for c in cases
                                   for tag in c["tags"] if tag in REQUIRED_ADVERSARIAL}),
        },
    }


def audit(cases: list) -> list:
    """Corpus integrity.  Anything this returns is a bug in the *data*, not the engine."""
    errors = []
    seen = set()
    for c in cases:
        cid = c.get("id") or "<missing id>"
        if cid in seen:
            errors.append(f"{cid}: duplicate case id")
        seen.add(cid)
        if not (c.get("title") or "").strip():
            errors.append(f"{cid}: empty title")
        if not c.get("person"):
            errors.append(f"{cid}: no person")
        tr = c["transcript"]
        if not tr.strip():
            errors.append(f"{cid}: empty transcript")
        if len(tr) < 60:
            errors.append(f"{cid}: transcript is too short to be a visit")
        ref = c.get("expected_refusal")
        if ref is not None and ref not in REFUSALS:
            errors.append(f"{cid}: expected_refusal must be one of {REFUSALS}, got {ref!r}")
        if ref and c["items"]:
            errors.append(f"{cid}: a refused visit must store nothing, so it may not have gold items")
        if not ref:
            m = ADULT_LINT.search(tr)
            if m:
                errors.append(f"{cid}: transcript looks paediatric ({m.group(0)!r}) -- corpus is adults only")
        for n, g in enumerate(c["items"]):
            where = f"{cid}.items[{n}]"
            if g["kind"] not in KINDS:
                errors.append(f"{where}: kind {g['kind']!r} is not in the closed vocabulary {KINDS}")
            q = (g.get("quote") or "").strip()
            if not q:
                errors.append(f"{where}: empty gold quote")
            elif not verbatim(q, tr):
                # The single most important property of a gold corpus: every gold quote
                # must be something the clinician actually said in this transcript.
                errors.append(f"{where}: gold quote is NOT verbatim in its transcript: {q[:70]!r}")
            sch = g.get("schedule", "absent")
            if sch in (None, "absent", "UNKNOWN"):
                pass
            elif not isinstance(sch, dict):
                errors.append(f"{where}: schedule must be an object, null or 'absent'")
            else:
                k = sch.get("kind")
                if k is not None and k not in SCHEDULE_KINDS:
                    errors.append(f"{where}: schedule kind {k!r} not in {SCHEDULE_KINDS}")
                for part in sch.get("day_parts", []):
                    if part not in SCHEDULE_PARTS:
                        errors.append(f"{where}: day_part {part!r} not in {SCHEDULE_PARTS}")
    if len(cases) < MIN_CASES:
        errors.append(f"corpus has {len(cases)} cases, the contract needs >= {MIN_CASES}")
    if sum(len(c["items"]) for c in cases) < MIN_GOLD_ITEMS:
        errors.append(f"corpus has {sum(len(c['items']) for c in cases)} gold items, "
                      f"the contract needs >= {MIN_GOLD_ITEMS}")
    for need in ("crisis", "child", "injection"):
        if not any(c.get("expected_refusal") == need for c in cases):
            errors.append(f"corpus is missing the required adversarial {need} case")
    if not any("empty" in c["tags"] for c in cases):
        errors.append("corpus is missing a nothing-actionable case (zero gold items)")
    return errors


def verbatim(quote: str, transcript: str) -> bool:
    """Same normalisation as careloop.extract.quote_guard -- whitespace and case only."""
    norm = lambda t: re.sub(r"\s+", " ", t or "").strip().casefold()
    return bool(norm(quote)) and norm(quote) in norm(transcript)
