"""timecraft — canonical time semantics (N22).

Normalises "morning", "after food", "every Friday", "in two weeks" into a structured
recurrence anchored to the recorded date, a configurable meal anchor and the household
time zone. Without this, "tomorrow" and "Friday" silently mean the wrong day.
"""
import datetime as dt
import re

PARTS = ("MORNING", "AFTER_FOOD", "BEFORE_FOOD", "MIDDAY", "AFTERNOON", "EVENING",
         "BEDTIME", "NIGHTWAKING", "DAILY", "WEEKLY", "MONTHLY", "ONCE", "ASNEEDED", "UNKNOWN")

MEAL_ANCHORS = {"BREAKFAST": "08:00", "LUNCH": "13:00", "DINNER": "20:00"}

PART_CLOCK = {
    "MORNING": ("07:00", "11:00"),
    "MIDDAY": ("12:00", "14:00"),
    "AFTERNOON": ("14:00", "17:00"),
    "EVENING": ("17:00", "21:00"),
    "BEDTIME": ("21:00", "23:00"),
    "NIGHTWAKING": ("02:00", "05:00"),
}

_DAYPART_WORDS = [
    (("empty stomach", "before breakfast", "before food", "before meal", "pre-meal", "before eating"), "BEFORE_FOOD"),
    (("after breakfast", "after food", "after meal", "after eating", "with food", "with meals", "after lunch", "after dinner", "with breakfast"), "AFTER_FOOD"),
    (("morning", "a.m.", "am", "sunrise", "upon waking", "when you wake"), "MORNING"),
    (("noon", "midday", "lunchtime", "lunch"), "MIDDAY"),
    (("afternoon",), "AFTERNOON"),
    (("evening", "pm", "p.m.", "dusk", "supper", "dinner", "tonight"), "EVENING"),
    (("bedtime", "night", "before bed", "at sleep", "before sleep"), "BEDTIME"),
    (("during the night", "night waking", "if awake at night", "whenever waking"), "NIGHTWAKING"),
]

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_schedule(text: str, anchor_date=None):
    """Return a canonical dict for a free-text schedule fragment."""
    t = (text or "").lower()
    parts = []
    for words, part in _DAYPART_WORDS:
        if any(w in t for w in words):
            if part not in parts:
                parts.append(part)
    every_x = None
    m = re.search(r"\b(?:every|each)\s+(\d+|two|three|four|six|eight|twelve)\s*(hour|hr|day|days|week|weeks|month|months)\b", t)
    if m:
        n = {"two": 2, "three": 3, "four": 4, "six": 6, "eight": 8, "twelve": 12}.get(m.group(1), m.group(1))
        every_x = {"n": int(n), "unit": m.group(2).rstrip("s")}
    times_per_day = None
    m2 = re.search(r"\b(\d+|one|two|three|four|x)\s*(?:times|x)\s+(?:a|per|the)\s+day\b", t)
    if m2:
        times_per_day = {"one": 1, "two": 2, "three": 3, "four": 4, "x": 2}.get(m2.group(1)) or int(m2.group(1))
    if times_per_day is None:
        if re.search(r"\bonce\s+(?:daily|a\s+day)\b", t):
            times_per_day = 1
        elif re.search(r"\btwice\s+(?:daily|a\s+day)\b", t):
            times_per_day = 2
        elif re.search(r"\bthrice\b|\bthree\s+times\s+(?:daily|a\s+day)\b", t):
            times_per_day = 3
    weekdays = sorted({d for name, d in _WEEKDAYS.items() if re.search(rf"\b{name}(day)?\b", t)})
    dates = re.findall(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", t)
    once_when = None
    mrel = re.search(r"\bin (\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve) (day|days|week|weeks)\b", t)
    if mrel and anchor_date:
        n = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "twelve": 12}.get(mrel.group(1), mrel.group(1))
        unit_days = 7 if "week" in mrel.group(2) else 1
        once_when = (dt.date.fromisoformat(anchor_date) + dt.timedelta(days=int(n) * unit_days)).isoformat()
    if "tomorrow" in t and anchor_date:
        once_when = once_when or (dt.date.fromisoformat(anchor_date) + dt.timedelta(days=1)).isoformat()
    if dates and anchor_date:
        once_when = once_when or _resolve_date(dates[0], anchor_date)
    if not parts and times_per_day:
        parts = ["MORNING", "NIGHTWAKING"] if times_per_day >= 3 else (["MORNING", "EVENING"] if times_per_day == 2 else ["MORNING"])
    if not parts and every_x:
        parts = ["ANYTIME"]
    kind = "RECURRENCE"
    if once_when:
        kind = "ONCE"
    elif weekdays:
        kind = "WEEKLY"
    elif every_x:
        kind = "INTERVAL"
    elif not parts:
        kind = "UNKNOWN"
    return {"kind": kind, "day_parts": parts or (["ANYTIME"] if kind == "INTERVAL" else ["UNKNOWN"]),
            "times_per_day": times_per_day, "every": every_x, "weekdays": weekdays,
            "once_on": once_when, "raw": (text or "")[:120],
            "tz_note": "anchor=recorded date, meal anchors configurable"}


def _resolve_date(s, anchor_iso):
    a = dt.date.fromisoformat(anchor_iso)
    bits = [int(b) for b in s.split("/")]
    if len(bits) == 2:
        mo, d = bits
        y = a.year if (mo, d) >= (a.month, a.day) else a.year + 1
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    return dt.date(bits[2] if bits[2] > 31 else 2000 + bits[2], bits[0], bits[1]).isoformat()


def expand_once(schedule, anchor_iso):
    """Return the single date a ONCE schedule points at, if any."""
    if schedule.get("once_on"):
        return schedule["once_on"]
    return None


def next_occurrences(schedule, anchor_iso, days=1):
    """Expand a canonical schedule into (date, day_part) tuples for the next `days` days."""
    out = []
    if schedule.get("kind") == "UNKNOWN":
        return out
    base = dt.date.fromisoformat(anchor_iso)
    for i in range(days):
        d = base + dt.timedelta(days=i)
        if schedule.get("kind") == "ONCE":
            if schedule.get("once_on") == d.isoformat():
                for p in schedule.get("day_parts", ["MORNING"]):
                    out.append((d.isoformat(), p))
            continue
        if schedule.get("weekdays") and d.weekday() not in schedule["weekdays"]:
            continue
        for p in schedule.get("day_parts", ["UNKNOWN"]):
            out.append((d.isoformat(), p))
    return out


def slot_order(part: str) -> int:
    order = {"NIGHTWAKING": 0, "BEFORE_FOOD": 1, "MORNING": 2, "MIDDAY": 3, "AFTER_FOOD": 4,
             "AFTERNOON": 5, "EVENING": 6, "BEDTIME": 7, "ANYTIME": 8, "UNKNOWN": 9}
    return order.get(part, 9)


def to_ics(person, text, schedule, anchor_iso, uid):
    """Minimal RFC 5545 export for ONCE items (E12)."""
    d = schedule.get("once_on") or anchor_iso
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CareLoop//EN", "BEGIN:VEVENT",
             f"UID:{uid}@careloop", f"DTSTAMP:{dt.date.fromisoformat(anchor_iso).strftime('%Y%m%d')}T000000Z",
             f"DTSTART;VALUE=DATE:{d.replace('-', '')}",
             f"SUMMARY:CareLoop follow-up for {person}", f"DESCRIPTION:{text[:200]}"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
