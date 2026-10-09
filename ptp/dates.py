"""Date logic for PTP. Standard library only; no AI, no network.

1. Pregnancy dates: estimated due date (LMP + 280 days), gestational age, trimester.
2. resolve(): turns Filipino/English phrases such as "sa susunod na Huwebes, alas nuwebe
   ng umaga" into a real date and time, relative to a reference date (the visit date).

Rules (kept simple on purpose, and covered by tests/test_dates.py):
  - A weekday with or without "susunod na" / "next" means the first such day STRICTLY AFTER
    the reference date. Said on a Thursday, "next Thursday" is 7 days later.
  - "susunod na linggo" / "next week" is the reference date plus 7 days.
  - A month and day with no year means the next time that date occurs on or after the
    reference date.
  - A clock time with no morning/afternoon word: hours 1-5 are taken as afternoon (clinic
    hours), 6-11 as morning, 12 as noon.
These are guesses made by code, so the app always shows the date for the user to confirm.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

# ---------------------------------------------------------------- pregnancy dates


def due_date(lmp: date) -> date:
    """Estimated due date: last menstrual period plus 280 days (an estimate, not the birth date)."""
    return lmp + timedelta(days=280)


def gestational_age(lmp: date, today: date) -> tuple[int, int]:
    """(completed weeks, extra days) since the LMP."""
    days = (today - lmp).days
    if days < 0:
        raise ValueError("The last menstrual period cannot be in the future.")
    return days // 7, days % 7


def trimester(weeks: int) -> int:
    """1: weeks 0-13, 2: weeks 14-27, 3: week 28 onward."""
    if weeks < 14:
        return 1
    if weeks < 28:
        return 2
    return 3


def progress_fraction(lmp: date, today: date) -> float:
    """Share of the 280 days that have passed, between 0 and 1."""
    return max(0.0, min(1.0, (today - lmp).days / 280.0))


# ---------------------------------------------------------------- word tables

NUMBERS = {
    "isa": 1, "isang": 1, "uno": 1, "una": 1, "one": 1,
    "dalawa": 2, "dalawang": 2, "dos": 2, "two": 2,
    "tatlo": 3, "tatlong": 3, "tres": 3, "three": 3,
    "apat": 4, "kwatro": 4, "kuwatro": 4, "four": 4,
    "lima": 5, "limang": 5, "singko": 5, "five": 5,
    "anim": 6, "sais": 6, "six": 6,
    "pito": 7, "pitong": 7, "siyete": 7, "seven": 7,
    "walo": 8, "walong": 8, "otso": 8, "eight": 8,
    "siyam": 9, "nuwebe": 9, "nine": 9,
    "sampu": 10, "sampung": 10, "diyes": 10, "ten": 10,
    "labing-isa": 11, "labingisa": 11, "onse": 11, "eleven": 11,
    "labindalawa": 12, "labing-dalawa": 12, "dose": 12, "twelve": 12,
}

WEEKDAYS = {
    "lunes": 0, "monday": 0, "mon": 0,
    "martes": 1, "tuesday": 1, "tue": 1, "tues": 1,
    "miyerkules": 2, "miyerkoles": 2, "wednesday": 2, "wed": 2,
    "huwebes": 3, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "biyernes": 4, "friday": 4, "fri": 4,
    "sabado": 5, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

MONTHS = {
    "enero": 1, "january": 1, "jan": 1,
    "pebrero": 2, "february": 2, "feb": 2,
    "marso": 3, "march": 3, "mar": 3,
    "abril": 4, "april": 4, "apr": 4,
    "mayo": 5,  # English "may" is left out on purpose: it is also the Filipino word "may" (there is)
    "hunyo": 6, "june": 6, "jun": 6,
    "hulyo": 7, "july": 7, "jul": 7,
    "agosto": 8, "august": 8, "aug": 8,
    "setyembre": 9, "september": 9, "sep": 9, "sept": 9,
    "oktubre": 10, "october": 10, "oct": 10,
    "nobyembre": 11, "november": 11, "nov": 11,
    "disyembre": 12, "december": 12, "dec": 12,
}

_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUM_RE = "|".join(sorted((re.escape(k) for k in NUMBERS), key=len, reverse=True))


def _num(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return NUMBERS.get(token)


# ---------------------------------------------------------------- time of day


def parse_time(text: str) -> Optional[str]:
    """First clock time found in the text as "HH:MM" (24 h), or None."""
    t = text.lower()

    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        pm = m.group(3).startswith("p")
        if hour == 12:
            hour = 12 if pm else 0
        elif pm:
            hour += 12
        if hour < 24 and minute < 60:
            return f"{hour:02d}:{minute:02d}"

    m = re.search(r"\b(?:alas|ala)[\s-]+(\d{1,2}|" + _NUM_RE + r")\b(.{0,28})", t)
    if m:
        hour = _num(m.group(1))
        tail = m.group(2)
        if hour is not None and 1 <= hour <= 12:
            if re.search(r"\b(hapon|gabi|afternoon|evening)\b", tail):
                hour = hour if hour == 12 else hour + 12
            elif re.search(r"\b(umaga|morning|madaling\s+araw)\b", tail):
                hour = 0 if hour == 12 else hour
            elif re.search(r"\btanghali\b", tail):
                hour = 12
            elif 1 <= hour <= 5:
                hour += 12  # clinic hours: "alas tres" means 3 pm
            return f"{hour:02d}:00"

    m = re.search(r"\b(\d{1,2})\s+ng\s+(umaga|hapon|gabi|tanghali)\b", t)
    if m:
        hour = int(m.group(1))
        period = m.group(2)
        if 1 <= hour <= 12:
            if period in ("hapon", "gabi") and hour != 12:
                hour += 12
            elif period == "tanghali":
                hour = 12
            elif period == "umaga" and hour == 12:
                hour = 0
            return f"{hour:02d}:00"

    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


# ---------------------------------------------------------------- dates


def _next_weekday(ref: date, weekday: int) -> date:
    delta = (weekday - ref.weekday()) % 7
    return ref + timedelta(days=delta or 7)


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve(text: str, ref: date) -> dict:
    """Find the first date phrase in text.

    Returns {"date": "YYYY-MM-DD" | None, "time": "HH:MM" | None, "matched": phrase | None}.
    The time is found independently, so "alas nuwebe" alone gives a time and no date.
    """
    low = text.lower()
    found: list[tuple[int, date, str]] = []  # (position, date, matched text)

    # Month and day: "Oktubre 15", "Oct 15, 2026", "15 ng Oktubre"
    for m in re.finditer(r"\b(" + _MONTH_RE + r")\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", low):
        month, day = MONTHS[m.group(1)], int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
        d = _build_month_day(ref, month, day, year)
        if d:
            found.append((m.start(), d, m.group(0)))
    for m in re.finditer(r"\b(\d{1,2})\s+(?:ng\s+)?(" + _MONTH_RE + r")\b(?:,?\s+(\d{4}))?", low):
        month, day = MONTHS[m.group(2)], int(m.group(1))
        year = int(m.group(3)) if m.group(3) else None
        d = _build_month_day(ref, month, day, year)
        if d:
            found.append((m.start(), d, m.group(0)))

    for m in re.finditer(r"\b(bukas|tomorrow)\b", low):
        found.append((m.start(), ref + timedelta(days=1), m.group(0)))
    for m in re.finditer(r"\b(makalawa|day after tomorrow)\b", low):
        found.append((m.start(), ref + timedelta(days=2), m.group(0)))
    for m in re.finditer(r"\b(ngayong araw|ngayon|today)\b", low):
        found.append((m.start(), ref, m.group(0)))

    for m in re.finditer(r"\b(?:sa\s+loob\s+ng|in|after)\s+(\d{1,2}|" + _NUM_RE + r")(?:\s+na)?\s+(araw|days?|linggo|weeks?)\b", low):
        n = _num(m.group(1))
        if n is not None:
            step = 1 if m.group(2).startswith(("araw", "day")) else 7
            found.append((m.start(), ref + timedelta(days=n * step), m.group(0)))

    for m in re.finditer(r"\b(susunod\s+na\s+linggo|next\s+week)\b", low):
        found.append((m.start(), ref + timedelta(days=7), m.group(0)))

    for m in re.finditer(r"\b(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", low):
        found.append((m.start(), _next_weekday(ref, WEEKDAYS[m.group(1)]), m.group(0)))
    # "Linggo" as Sunday only when written with a capital L and not part of "susunod na linggo".
    for m in re.finditer(r"(?<!susunod na )\bLinggo\b", text):
        found.append((m.start(), _next_weekday(ref, 6), m.group(0)))

    result_date, matched = None, None
    if found:
        found.sort(key=lambda item: item[0])
        _, d, matched = found[0]
        result_date = d.isoformat()
    return {"date": result_date, "time": parse_time(text), "matched": matched}


def _build_month_day(ref: date, month: int, day: int, year: Optional[int]) -> Optional[date]:
    if year is not None:
        return _safe_date(year, month, day)
    d = _safe_date(ref.year, month, day)
    if d is None:
        return None
    if d < ref:
        d = _safe_date(ref.year + 1, month, day)
    return d
