"""policy_guard — the Alexa+ compliance rules expressed as unit-testable code (E6/D1/D8).

One module, imported by every surface: MCP tool responses, cards, resources and the
companion API all pass their outbound text through `guard_payload()` before it leaves
the process. The Alexa surface is *dose-free by construction*: `mask_doses()` runs on
every string field the MCP server can return.
"""
import html
import re

DISCLAIMER = ("This is not medical advice. CareLoop only repeats what your clinician said.")
DOSE_POLICY = ("CareLoop never shows or speaks medicine amounts on the Alexa surface. "
               "Open the CareLoop companion page to see the exact amounts your clinician recorded.")
ATTRIBUTION_TEMPLATE = "According to your clinician, as recorded on {date}."
GUEST_NOTICE = "Link your CareLoop household account to ask about a care plan."
NO_SOURCE = "I don't have a recording about that."

MASK_TOKEN = "your prescribed amount"

_UNITS = r"(?:mg|mcg|µg|ug|g|gm|gram|grams|kg|ml|mL|l|L|litre|litres|liter|liters|" \
         r"IU|iu|units?|tablet|tablets|pill|pills|capsule|capsules|drop|drops|puff|puffs|" \
         r"spray|sprays|injection|injections|dose|doses|mg\/kg|mcg\/kg)"

# number, numeric fractions, and spelled quantities.
# NOTE: bare spelled "one" is excluded (matches "one week"); bare digits with a
# non-medicine unit (litres, minutes) can over-mask — masking is conservative by
# design and the residual behaviour is measured by the dose-leak / over-mask metrics.
_SPOKEN = r"(?:(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|" \
          r"twenty|thirty|forty|fifty|hundred|thousand)(?:\s+and\s+(?:a\s+)?(?:half|quarter))?" \
          r"|(?:one|a|an)\s+and\s+a\s+half)"
_NUM = rf"(?:\d+(?:[.,]\d+)?(?:\s*\/\s*\d+)?|(?:one|a|an)\s+half|half\s+(?:a|an)|{_SPOKEN})"

# number + unit  →  "500 mg", "500mg", "two tablets", "1.5 ml", "five hundred milligrams"
_RE_NUM_UNIT = re.compile(
    rf"{_NUM}\s*(?:{_UNITS}|(?:milli)?grams?|milliliters?|milligrams?)\b(?:\s*(?:a|per|every|twice|three|four|times)\s+\w+)?",
    re.IGNORECASE)

# unit + number  →  "mg 500" (rare)
_RE_UNIT_NUM = re.compile(rf"\b(?:{_UNITS})\s*{_NUM}\b", re.IGNORECASE)

# strength notation → "Metformin 500mg", "Vitamin D 60000 IU"
_RE_STRENGTH = re.compile(rf"[A-Za-z][A-Za-z0-9-]+\s+{_NUM}\s*(?:{_UNITS}|(?:milli)?grams?)?\b(?=\s|[,.;]|$)",
                          re.IGNORECASE)

# "take 2", "take two" (bare count after a dosing verb)
_BARE_QTY = rf"(?:{_SPOKEN}|(?:one|a)\s+half|\d+(?:[.,]\d+)?)"
_RE_BARE_COUNT = re.compile(
    rf"\b(?:take|using|apply|instil(?:l)?|administer|give)\s+{_BARE_QTY}\b", re.IGNORECASE)

# frequency with an implied amount  →  "1-0-1", "1/2 tablet"
_RE_DOSING_GRID = re.compile(r"\b\d\s*[-–]\s*\d\s*[-–]\s*\d\b")

_DOSE_RES = (_RE_NUM_UNIT, _RE_UNIT_NUM, _RE_BARE_COUNT, _RE_DOSING_GRID)

_CONTAINS_UNITS = re.compile(rf"(?:\b|(?<=\d))(?:{_UNITS})(?![A-Za-z])", re.IGNORECASE)


def contains_dose(text: str) -> bool:
    """True if the string appears to carry a medicine amount."""
    if not text:
        return False
    for rx in _DOSE_RES:
        if rx.search(text):
            return True
    # strength fallback: capitalised token followed by number+unit inside 40 chars
    for m in _RE_STRENGTH.finditer(text):
        if _CONTAINS_UNITS.search(m.group(0)):
            return True
    return False


def mask_doses(text: str) -> str:
    """Replace every dose token with a constant phrase. Idempotent."""
    if not text:
        return text
    out = _RE_DOSING_GRID.sub(MASK_TOKEN, text)
    out = _RE_NUM_UNIT.sub(MASK_TOKEN, out)
    out = _RE_UNIT_NUM.sub(MASK_TOKEN, out)
    out = _RE_BARE_COUNT.sub(MASK_TOKEN, out)
    out = re.sub(rf"(?:{re.escape(MASK_TOKEN)})(?:\s+{re.escape(MASK_TOKEN)})+", MASK_TOKEN,
                 out, flags=re.IGNORECASE)
    return out


def mask_deep(obj):
    """mask_doses over every string in a nested structure (Part C: every field)."""
    if isinstance(obj, str):
        return mask_doses(obj)
    if isinstance(obj, list):
        return [mask_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_deep(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- crisis (§13)
_CRISIS = re.compile(
    r"\b(?:kill (?:myself|them|himself|herself)|want(?:s|ed)? to (?:die|be dead)|"
    r"end (?:my|their|his|her) life|"
    r"end(?:ing)? it all|no reason to (?:live|go on)|can'?t (?:go|keep) (?:on|going) (?:anymore)?|"
    r"suicide|suicidal|self[- ]?harm|hurt (?:myself|himself|herself)|cut (?:my|his|her) wrists|"
    r"poison(?:ed)? my(?:self|son|daughter)|overdose on purpose|take all the pills|"
    r"better off dead|hang(?:ing)? my(?:self|s?e?l?f))\b",
    re.IGNORECASE)

CRISIS_REDIRECT = ("If anyone is in danger right now, please call your local emergency number. "
                   "In India you can call 112, or the Tele-MANAS mental-health helpline 14416 (toll-free, 24x7). "
                   "I have not saved this as a care item.")


def is_crisis(text: str) -> bool:
    return bool(text) and bool(_CRISIS.search(text))


# ---------------------------------------------------------------- child safety (§4)
_CHILD = re.compile(
    r"\b(?:my (?:son|daughter|child|kid|baby|grandson|granddaughter|grandchild|grandkid)|"
    r"grandchildren?|grandson|granddaughter|grandkid|toddler|infant|pediatric|paediatric|school[- ]age|"
    r"\b(?:child|kid|minor)s?\b|age [0-9]{1,2}\b|grades? [1-9]\b|diaper|nursery|kindergarten)\b",
    re.IGNORECASE)


def mentions_child(text: str) -> bool:
    return bool(text) and bool(_CHILD.search(text))


# ---------------------------------------------------------------- prompt injection (§12)
_INJECTION = re.compile(
    r"\b(?:(?:ignore|disregard|forget|override)\s+(?:(?:all|any|the|these|those|your|system|"
    r"previous|prior|earlier|preceding|above)\s+)*"
    r"(?:instructions?|rules?|prompts?|directives?|guidance)|"
    r"you are now|new (?:system )?instruction|"
    r"act as (?:if|an? (?:unrestricted|admin|root|developer|system))|"
    r"pretend to be|system prompt|"
    r"reveal (?:your|the) (?:prompt|instructions|doses)|jailbreak|DAN mode)\b", re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(text) and bool(_INJECTION.search(text))


# ---------------------------------------------------------------- outbound guard
def guard_payload(structured: dict, *, surface: str = "alexa", require_attribution: bool = True) -> dict:
    """Final gate for anything the MCP server returns on the Alexa surface."""
    out = dict(structured or {})
    if surface == "alexa":
        out = mask_deep(out)
        out.setdefault("disclaimer", DISCLAIMER)
        out.setdefault("dose_policy", DOSE_POLICY)
        if require_attribution and "attribution" not in out:
            out["attribution"] = "According to your clinician, as recorded in CareLoop."
    return out


def dose_leak_in(obj) -> list:
    """Return list of field paths that still contain dose tokens (used by eval + judge)."""
    leaks = []

    def walk(x, path):
        if isinstance(x, str):
            if contains_dose(x):
                leaks.append((path, x))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else k)

    walk(obj, "")
    return leaks


def escape(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


# ---------------------------------------------------------------- speaker scope (simulated voice profile)
# The two keys a device uses to say who is speaking, and the value that labels the claim as what
# it is. `scripts/make_addon.py` generates `voiceProfile.assertion` in the shipped interaction
# model FROM these constants, so the device and this service cannot drift apart on a field name.
SPEAKER_META_KEY = "careloop/speaker"
VOICE_PROFILE_META_KEY = "careloop/voiceProfile"
SIMULATED_PROFILE_VALUE = "careloop-simulated/1"

SPEAKER_BAD_PROFILE = ("This device hasn't told me who is speaking in a way I can rely on, so I won't "
                       "read the plan out.")
SPEAKER_BAD_PROFILE_NEXT = "Open the CareLoop companion page to see the plan there."
SPEAKER_UNKNOWN = "I can't match that name to someone in this household, so I won't answer as them."
SPEAKER_UNKNOWN_NEXT = "Ask your household to add that person on the CareLoop companion page."
SPEAKER_OUT_OF_SCOPE = ("That part of the plan isn't yours to hear. The household decides who hears "
                        "what.")
SPEAKER_OUT_OF_SCOPE_NEXT = ("Open the CareLoop companion page to see the full plan, or ask as someone "
                             "the household lets hear it.")
SPEAKER_PENDING_HELD = ("That instruction is still waiting to be confirmed by your clinician, and this "
                        "household doesn't read those out to you.")
SPEAKER_PENDING_HELD_NEXT = SPEAKER_OUT_OF_SCOPE_NEXT


def resolve_claim(meta: dict, people: list):
    """What a device's speaker claim resolves to, against the household's own people.

    Verdicts: 'none' (the device claimed nothing — the household is answered as it always was),
    'bad-profile' (a claim arrived without the profile label that names it as a claim), 'unknown'
    (no person or alias in this household matches) or 'ok' with the canonical name."""
    claim = str((meta or {}).get(SPEAKER_META_KEY) or "").strip()
    if not claim:
        return "none", None
    if (meta or {}).get(VOICE_PROFILE_META_KEY) != SIMULATED_PROFILE_VALUE:
        return "bad-profile", None
    key = claim.lower()
    for p in people or []:
        if str(p.get("name", "")).lower() == key or key in [str(a).lower() for a in p.get("aliases") or []]:
            return "ok", p["name"]
    return "unknown", None


def scope_entry(scope: list, speaker: str):
    """The boundary this household stored for that speaker, or None when they set none.
    A speaker with no entry is bounded by nothing, so a household that never configured the
    feature is answered exactly as before."""
    key = (speaker or "").strip().lower()
    for entry in scope or []:
        if str(entry.get("speaker", "")).strip().lower() == key:
            return entry
    return None


def may_hear_people(entry: dict, person, everyone) -> bool:
    """Whether the speaker may have `person` read out. `person` is None when the call is about the
    whole household, which is allowed only if every person in it falls inside the boundary."""
    if not entry:
        return True
    heard = {str(n).strip().lower() for n in (entry.get("may_hear") or [])}
    if person is None:
        return all(str(n).strip().lower() in heard for n in (everyone or []))
    return str(person).strip().lower() in heard


def reveals_pending(structured) -> bool:
    """True when any item in a response carries `needs_confirm` — an instruction still waiting on
    the clinician. Read-only responses only; a refusal after a write would be a lie about state."""
    if isinstance(structured, dict):
        if structured.get("needs_confirm"):
            return True
        return any(reveals_pending(v) for v in structured.values())
    if isinstance(structured, list):
        return any(reveals_pending(v) for v in structured)
    return False


# ---------------------------------------------------------------- metadata checks (§8/§13 of QuickStart)
LIMITS = {"name": 30, "shortDescription": 123, "fullDescription": 4000, "examplePhrase": 200}
_BANNED_KEYWORDS = re.compile(r"\b(amazon|alexa|echo|fire tv|ring)\b", re.IGNORECASE)


def validate_store_listing(listing: dict) -> list:
    """Return a list of human-readable metadata problems; empty means pass."""
    problems = []
    loc = (listing.get("storeListing", {}).get("locales", {}) or {})
    for code, l in loc.items():
        for key, lim in (("name", LIMITS["name"]), ("shortDescription", LIMITS["shortDescription"]),
                         ("fullDescription", LIMITS["fullDescription"])):
            val = (l.get(key) or {}).get("value") if key == "name" else l.get(key)
            if val is None:
                problems.append(f"{code}: missing {key}")
            elif len(val) > lim:
                problems.append(f"{code}: {key} is {len(val)} chars, limit {lim}")
        for p in l.get("examplePhrases", []):
            if len(p) > LIMITS["examplePhrase"]:
                problems.append(f"{code}: example phrase over {LIMITS['examplePhrase']} chars")
        if len(l.get("examplePhrases", [])) not in (3, 4):
            problems.append(f"{code}: needs 3-4 example phrases")
        media = l.get("mediaAssets", {})
        icons = media.get("icons", {}).get("light", [])
        sizes = {i.get("size") for i in icons if isinstance(i, dict)}
        need = {"72x72", "64x64", "88x88", "126x126", "180x180", "241x241"}
        if not need <= sizes:
            problems.append(f"{code}: missing icon sizes {sorted(need - sizes)}")
        car = media.get("carouselImages", [])
        if not any((c.get("size") == "600x900") for c in car):
            problems.append(f"{code}: needs at least one 600x900 carousel image")
        pc = l.get("privacyAndCompliance", {})
        for u in ("privacyPolicyUrl", "termsOfUseUrl"):
            val = pc.get(u, "")
            if "<" in val or "your-host" in val:
                problems.append(f"{code}: placeholder URL in {u}")
    name = ""
    for l in loc.values():
        name = (l.get("name") or {}).get("value", "")
    if _BANNED_KEYWORDS.search(name or ""):
        problems.append("name contains a protected brand term")
    return problems
