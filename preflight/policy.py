"""policy — the audit's own dose detector and voice-hygiene rules.

This is an *independent* second implementation of the "voice surface must be dose-free"
kill criterion (it deliberately does not import careloop.policy_guard): if the server's
masking regex missed a quantity, the auditor's regex still has to catch it.
"""
import re

MASK_PHRASE = "your prescribed amount"

_UNITS = (r"mg/kg|mcg/kg|mg|mcg|ug|\u00b5g|grams?|gm|g|kg|ml|mL|cc|litres?|liters?|"
          r"litre|liter|l|IU|iu|units?|tablets?|pills?|capsules?|drops?|puffs?|"
          r"sprays?|injections?|doses?|suppositories?|measures?|spoons?")
_SPOKEN_NUM = (r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
               r"twenty|thirty|forty|fifty|hundred|thousand)(?:\s+and\s+(?:a\s+)?(?:half|quarter))?")
_NUM = r"\d+(?:[.,]\d+)?"

_RE_NUM_UNIT = re.compile(r"(?<![A-Za-z0-9])%s\s*(?:%s)(?![A-Za-z])" % (_NUM, _UNITS), re.I)
_RE_UNIT_NUM = re.compile(r"\b(?:%s)\s*%s\b" % (_UNITS, _NUM), re.I)
_RE_WORD_UNIT = re.compile(r"\b(?:%s)\s+(?:%s)(?![A-Za-z])" % (_SPOKEN_NUM, _UNITS), re.I)
# a dosing grid is 1-2 digits per slot ("1-0-1", "2-1-0"); an ISO date is not
_RE_GRID = re.compile(r"\b\d{1,2}\s*[-\u2013]\s*\d{1,2}\s*[-\u2013]\s*\d{1,2}\b")
_RE_TAKE = re.compile(r"\b(?:take|taking|apply|instill|instil|administer|give|inject|swallow)"
                      r"\s+(?:%s|%s)\b" % (_NUM, _SPOKEN_NUM), re.I)
_RE_HALF = re.compile(r"\b(?:half|a\s+half|a\s+quarter)\s+(?:a|an)?\s*(?:%s)(?![A-Za-z])" % _UNITS, re.I)
_RE_STRENGTH = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{2,}\s+%s\s*(?:%s)(?![A-Za-z])\b" % (_NUM, _UNITS), re.I)

_PATTERNS = (_RE_NUM_UNIT, _RE_UNIT_NUM, _RE_WORD_UNIT, _RE_GRID, _RE_TAKE, _RE_HALF, _RE_STRENGTH)

_REF_ID = re.compile(r"\bi-[0-9a-f]{4,}\b", re.I)
_HEX_ID = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_INTERNAL_KEYS = re.compile(r"\b(?:structuredContent|outputSchema|inputSchema|jsonrpc|"
                            r"readOnlyHint|destructiveHint|idempotentHint|isError|arguments|refs?|"
                            r"session[_ ]id|link[_ ]id|token[_ ]hash|transcript[_ ]hash|"
                            r"dedup[_ ]key|access[_ ]token|refresh[_ ]token|household[_ ]code)\b",
                            re.I)
_STRAY_ID = re.compile(r"(?<![A-Za-z])id(?![A-Za-z0-9])", re.I)


def _redact_ids(text):
    """Blank out opaque identifiers before a dose scan.

    A hex item id such as `i-740cc822` contains the substring `740cc`, which the
    number+unit regex reads as "740 cc". These are identifiers, not medicine amounts --
    voice_hygiene already flags the very same shapes as "a raw id" -- so they must not be
    mistaken for a dose leak. Real spoken quantities ("take 500 mg") contain spaces and
    letters no id shape matches, so this only removes false positives, never a true leak.
    """
    for rx in (_UUID, _REF_ID, _HEX_ID):
        text = rx.sub(" ID ", text)
    return text


def contains_dose(text):
    """True when a string still carries a medicine quantity."""
    if not text:
        return False
    text = _redact_ids(text)
    return any(rx.search(text) for rx in _PATTERNS)


def dose_leaks(obj, path=""):
    """[(path, sample)] for every string in a nested structure that carries a dose."""
    leaks = []
    if isinstance(obj, str):
        if contains_dose(obj):
            leaks.append((path or "<root>", obj[:120]))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            leaks.extend(dose_leaks(v, "%s.%s" % (path, k) if path else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            leaks.extend(dose_leaks(v, "%s[%d]" % (path, i)))
    return leaks


def result_leaks(reply_json):
    """Scan a whole tools/call result: spoken text, structuredContent, _meta."""
    result = (reply_json or {}).get("result")
    if not isinstance(result, dict):
        return []
    return dose_leaks(result)


def structured(reply_json):
    return ((reply_json or {}).get("result") or {}).get("structuredContent") or {}


def err_of(reply_json):
    """The JSON-RPC error object as a dict; HTTP-layer errors are plain strings here."""
    e = (reply_json or {}).get("error")
    if isinstance(e, dict):
        return e
    if e is None:
        return {}
    return {"code": None, "message": str(e)}


def spoken_of(reply_json):
    result = (reply_json or {}).get("result") or {}
    parts = [c.get("text", "") for c in (result.get("content") or []) if isinstance(c, dict)]
    return " ".join(p for p in parts if p)


def voice_hygiene(text, tool_names=()):
    """Problems with a string Alexa would speak: internal names, ids, JSON markup."""
    t = text or ""
    problems = []
    for name in tool_names:
        if re.search(r"\b%s\b" % re.escape(name), t):
            problems.append("names the tool '%s' out loud" % name)
    m = _INTERNAL_KEYS.search(t)
    if m:
        problems.append("leaks an internal key '%s'" % m.group(0))
    for rx, label in ((_REF_ID, "item id"), (_HEX_ID, "opaque id"), (_UUID, "uuid")):
        hit = rx.search(t)
        if hit:
            problems.append("speaks a raw %s (%s)" % (label, hit.group(0)[:20]))
    if _STRAY_ID.search(t):
        problems.append("says the word 'id'")
    if "{" in t or "}" in t or '["' in t:
        problems.append("contains JSON markup")
    if len(t.split()) > 80:
        problems.append("spoken answer is %d words, too long for voice" % len(t.split()))
    return problems


def description_hygiene(text, tool_names=()):
    """Tool descriptions are read aloud by some assistants: no internals, no ids either."""
    t = text or ""
    problems = []
    for name in tool_names:
        if re.search(r"\b%s\b" % re.escape(name), t):
            problems.append("references another tool by name (%s)" % name)
    m = _INTERNAL_KEYS.search(t)
    if m:
        problems.append("mentions an internal key (%s)" % m.group(0))
    hit = _REF_ID.search(t) or _UUID.search(t)
    if hit:
        problems.append("embeds a raw id (%s)" % hit.group(0)[:20])
    return problems
