import calendar
import re
from datetime import date, timedelta


# --------------------------
# Helpers for weekday/month logic
# --------------------------
WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}  # monday=0
WEEKDAYS.update({name.lower(): i for i, name in enumerate(calendar.day_abbr)})

# Convenience weekday sets
WEEKEND = {"saturday", "sunday", "sat", "sun"}
WEEKDAY_NAMES = set(WEEKDAYS.keys())  # includes abbrs
# Plural-friendly map (e.g., "mondays" → "monday")
WEEKDAY_SINGULAR = {w: w for w in WEEKDAYS}
WEEKDAY_SINGULAR.update({w + "s": w for w in WEEKDAYS})


MONTHS = {}
for i, name in enumerate(calendar.month_name):
    if i == 0:
        continue
    MONTHS[name.lower()] = i
for i, abbr in enumerate(calendar.month_abbr):
    if i == 0:
        continue
    MONTHS[abbr.lower()] = i
# Allow dotted abbreviations like "Dec."
MONTHS.update({f"{k}.": v for k, v in list(MONTHS.items()) if len(k) == 3})

MODS = {"this", "next", "coming", "upcoming", "last", "previous"}


def add_months(d: date, n: int) -> date:
    y, m = d.year, d.month
    m += n
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def clamp_day(y: int, m: int, day: int) -> date:
    day = min(day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    first_w = first.weekday()
    delta = (weekday - first_w + 7) % 7
    day = 1 + delta + (n - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        raise ValueError("n is too large for this month")
    return date(year, month, day)


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    delta = (last.weekday() - weekday + 7) % 7
    return last - timedelta(days=delta)


# Easter (Gregorian) — Meeus/Jones/Butcher algorithm
def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    x = (32 + 2 * e + 2 * i - h - k) % 7
    y = (a + 11 * h + 22 * x) // 451
    month = (h + x - 7 * y + 114) // 31
    day = 1 + ((h + x - 7 * y + 114) % 31)
    return date(year, month, day)


# ----------------------------------
# Holiday resolvers (U.S., common)
# ----------------------------------
def _fixed(month, day):
    return lambda y: date(y, month, day)


def _nth(mon, weekday, n):
    return lambda y: nth_weekday_of_month(y, mon, weekday, n)


def _last(mon, weekday):
    return lambda y: last_weekday_of_month(y, mon, weekday)


HOLIDAYS = {
    # Fixed-date
    "new year's day": _fixed(1, 1),
    "new years day": _fixed(1, 1),
    "new year's eve": _fixed(12, 31),
    "new years eve": _fixed(12, 31),
    "valentine's day": _fixed(2, 14),
    "valentines day": _fixed(2, 14),
    "saint patrick's day": _fixed(3, 17),
    "st patrick's day": _fixed(3, 17),
    "st patricks day": _fixed(3, 17),
    "st. patrick's day": _fixed(3, 17),
    "st. patricks day": _fixed(3, 17),
    "halloween": _fixed(10, 31),
    "independence day": _fixed(7, 4),
    "juneteenth": _fixed(6, 19),
    "veterans day": _fixed(11, 11),
    "christmas": _fixed(12, 25),
    "christmas day": _fixed(12, 25),
    "christmas eve": _fixed(12, 24),
    # Floating U.S.
    "mlk day": _nth(1, WEEKDAYS["monday"], 3),
    "martin luther king jr day": _nth(1, WEEKDAYS["monday"], 3),
    "presidents day": _nth(2, WEEKDAYS["monday"], 3),
    "memorial day": _last(5, WEEKDAYS["monday"]),
    "labor day": _nth(9, WEEKDAYS["monday"], 1),
    "columbus day": _nth(10, WEEKDAYS["monday"], 2),
    "indigenous peoples day": _nth(10, WEEKDAYS["monday"], 2),
    "thanksgiving": _nth(11, WEEKDAYS["thursday"], 4),
    "mothers day": _nth(5, WEEKDAYS["sunday"], 2),
    "mother's day": _nth(5, WEEKDAYS["sunday"], 2),
    "fathers day": _nth(6, WEEKDAYS["sunday"], 3),
    "father's day": _nth(6, WEEKDAYS["sunday"], 3),
    "easter": easter_sunday,
}


# Canonicalization for keys
def _canon(s: str) -> str:
    s = s.lower().strip()
    # normalize unicode dashes to ASCII hyphen
    s = re.sub(r"[–—-−]", "-", s)  # en/em/non-breaking/minus → "-"
    # keep apostrophes, dots (Dec.), AND hyphens (11-14)
    s = re.sub(r"[^\w\s',.-]", " ", s)
    # treat \"next calendar month\" the same as \"next month\" for parsing
    s = re.sub(r"\bcalendar\s+(?=month\b)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_ordinal_day(tok: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", tok)
    return int(m.group(1)) if m else None


def _normalize_modifier(mod: str | None) -> str:
    mod = (mod or "").lower().strip()
    return "next" if mod == "upcoming" else mod


# ----------------------------------
# Core selection logic
# ----------------------------------
def _choose_occurrence(target_this_year: date, base: date, modifier: str) -> date:
    modifier = _normalize_modifier(modifier)
    if modifier in ("next", "coming"):
        return target_this_year if target_this_year > base else target_this_year.replace(year=target_this_year.year + 1)
    if modifier == "this":
        return (
            target_this_year if target_this_year >= base else target_this_year.replace(year=target_this_year.year + 1)
        )
    if modifier in ("last", "previous"):
        return target_this_year if target_this_year < base else target_this_year.replace(year=target_this_year.year - 1)
    # default: upcoming/on-or-after
    return target_this_year if target_this_year >= base else target_this_year.replace(year=target_this_year.year + 1)


# ----------------------------------
# Public API
# ----------------------------------
def parse_relative_date(text: str, base: date | None = None, return_iso: bool = True) -> str | date:
    """
    Parse a short relative-date description to a concrete date.

    Supported (permissive):
      - 'upcoming Friday', 'the next Monday', 'upcoming Thanksgiving' (alias of 'next')
      - 'on the 26th next month', '26th next month', '26th of the next month'
      - '15th in 3 months'
      - 'next Dec. 3rd', 'this September 1', 'last Jul 4th'
      - 'the 3rd next December', '3rd next December', 'the 3rd of December', 'the 3rd of December next'
      - Weekdays: '(the)? (this|next|upcoming|last) <weekday>'
      - Holidays: '(the)? (this|next|upcoming|last) <holiday>' and 'in N {days|weeks|months|years}'
    """
    if base is None:
        base = date.today()

    raw = text.strip()
    s = _canon(raw)

    # ----------------------------
    # A) Month + day: "next Dec. 3rd" / "this september 1" / "last jul 4th"
    # ----------------------------
    m = re.fullmatch(r"(this|next|coming|upcoming|last|previous)?\s*([a-z.]+)\s+(\d{1,2}(?:st|nd|rd|th)?)", s)
    if m and m.group(2) in MONTHS:
        modifier = _normalize_modifier(m.group(1))
        month = MONTHS[m.group(2)]
        day = _parse_ordinal_day(m.group(3))
        try_this = clamp_day(base.year, month, day)
        chosen = _choose_occurrence(try_this, base, modifier)
        return chosen.isoformat() if return_iso else chosen

    # ----------------------------
    # B1) Day + 'of' + Month with leading modifier:
    #     "this the 3rd of december" / "next 3rd of december"
    # ----------------------------
    m = re.fullmatch(
        r"(this|next|coming|upcoming|last|previous)\s*(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?([a-z.]+)",
        s,
    )
    if m and m.group(3) in MONTHS:
        modifier = _normalize_modifier(m.group(1))
        day = _parse_ordinal_day(m.group(2))
        month = MONTHS[m.group(3)]
        try_this = clamp_day(base.year, month, day)
        chosen = _choose_occurrence(try_this, base, modifier)
        return chosen.isoformat() if return_iso else chosen

    # B2) Day + Month + trailing modifier:
    #     "the 3rd of december next" / "3rd december upcoming"
    m = re.fullmatch(
        r"(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?([a-z.]+)\s+(this|next|coming|upcoming|last|previous)",
        s,
    )
    if m and m.group(2) in MONTHS:
        day = _parse_ordinal_day(m.group(1))
        month = MONTHS[m.group(2)]
        modifier = _normalize_modifier(m.group(3))
        try_this = clamp_day(base.year, month, day)
        chosen = _choose_occurrence(try_this, base, modifier)
        return chosen.isoformat() if return_iso else chosen

    # B3) Day + modifier + Month:
    #     "the 3rd next december" / "3rd next december"
    m = re.fullmatch(
        r"(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+(this|next|coming|upcoming|last|previous)\s+([a-z.]+)", s
    )
    if m and m.group(3) in MONTHS:
        day = _parse_ordinal_day(m.group(1))
        modifier = _normalize_modifier(m.group(2))
        month = MONTHS[m.group(3)]
        try_this = clamp_day(base.year, month, day)
        chosen = _choose_occurrence(try_this, base, modifier)
        return chosen.isoformat() if return_iso else chosen

    # B4) Day + 'of' + Month with NO modifier:
    #     "the 3rd of december" / "3rd of dec." / "3rd december"
    m = re.fullmatch(r"(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?([a-z.]+)", s)
    if m and m.group(2) in MONTHS:
        day = _parse_ordinal_day(m.group(1))
        month = MONTHS[m.group(2)]
        try_this = clamp_day(base.year, month, day)
        # no modifier -> default behavior: upcoming/on-or-after base
        chosen = _choose_occurrence(try_this, base, modifier="")
        return chosen.isoformat() if return_iso else chosen

    # ----------------------------
    # C) "<D> of the <mod> month" AND loose variants:
    #     "26th of the next month" (already) + "on the 26th next month" / "26th next month"
    # ----------------------------
    m = re.fullmatch(
        r"(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+(?:of\s+)?(?:the\s+)?(this|next|coming|upcoming|last|previous)\s+month",
        s,
    )
    if m:
        day = _parse_ordinal_day(m.group(1))
        mod = _normalize_modifier(m.group(2))
        shift = 0 if mod == "this" else (1 if mod in ("next", "coming") else (-1 if mod in ("last", "previous") else 1))
        target = add_months(base, shift)
        out = clamp_day(target.year, target.month, day)
        return out.isoformat() if return_iso else out

    # Keep the original strict "of the <mod> month" for safety
    m = re.fullmatch(r"(\d{1,2}(?:st|nd|rd|th)?)\s+of\s+the\s+(this|next|coming|upcoming|last|previous)\s+month", s)
    if m:
        day = _parse_ordinal_day(m.group(1))
        mod = _normalize_modifier(m.group(2))
        shift = 0 if mod == "this" else (1 if mod in ("next", "coming") else -1)
        target = add_months(base, shift)
        out = clamp_day(target.year, target.month, day)
        return out.isoformat() if return_iso else out

    # ----------------------------
    # D) "<D> in N months"
    # ----------------------------
    m = re.fullmatch(r"(?:on\s+)?(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?)\s+in\s+(\d+)\s+months?", s)
    if m:
        day = _parse_ordinal_day(m.group(1))
        n = int(m.group(2))
        target = add_months(base, n)
        out = clamp_day(target.year, target.month, day)
        return out.isoformat() if return_iso else out

    # ----------------------------
    # E) "in N units" (days/weeks/months/years)
    # ----------------------------
    m = re.fullmatch(r"in\s+(\d+)\s+(day|days|week|weeks|month|months|year|years)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("day"):
            out = base + timedelta(days=n)
        elif unit.startswith("week"):
            out = base + timedelta(weeks=n)
        elif unit.startswith("month"):
            out = add_months(base, n)
        else:
            y = base.year + n
            d = min(base.day, calendar.monthrange(y, base.month)[1])
            out = date(y, base.month, d)
        return out.isoformat() if return_iso else out

    # ----------------------------
    # F) Weekdays: "(this|next|upcoming|last) <weekday>"
    # ----------------------------
    m = re.fullmatch(r"(?:the\s+)?(this|next|coming|upcoming|last|previous)?\s*([a-z]+)", s)
    if m and m.group(2) in WEEKDAYS:
        modifier = _normalize_modifier(m.group(1))
        target_wd = WEEKDAYS[m.group(2)]
        base_wd = base.weekday()
        if modifier in ("next", "coming", ""):  # treat bare weekday as upcoming (future strictly)
            delta = (target_wd - base_wd) % 7
            delta = 7 if delta == 0 else delta
            out = base + timedelta(days=delta)
        elif modifier == "this":  # same-week, can be today
            delta = (target_wd - base_wd) % 7
            out = base + timedelta(days=delta)
        else:  # last/previous
            delta = (base_wd - target_wd) % 7
            delta = 7 if delta == 0 else delta
            out = base - timedelta(days=delta)
        return out.isoformat() if return_iso else out

    # ----------------------------
    # G) Holidays: "(this|next|upcoming|last) <holiday>"
    # ----------------------------
    hm = re.fullmatch(r"(?:the\s+)?(this|next|coming|upcoming|last|previous)?\s*(.+)", s)
    if hm:
        modifier = _normalize_modifier(hm.group(1))
        holiday_name = hm.group(2).strip()
        candidates = [
            holiday_name,
            holiday_name.replace("’", "'"),
            re.sub(r" day$", "", holiday_name),
        ]
        for cand in candidates:
            key = _canon(cand)
            if key in HOLIDAYS:
                resolver = HOLIDAYS[key]  # function(year) -> date
                y = base.year
                d_this = resolver(y)

                if modifier in ("next", "coming"):
                    out = d_this if d_this > base else resolver(y + 1)
                elif modifier == "this":
                    out = d_this if d_this >= base else resolver(y + 1)
                elif modifier in ("last", "previous"):
                    out = d_this if d_this < base else resolver(y - 1)
                else:  # no modifier -> upcoming/on-or-after
                    out = d_this if d_this >= base else resolver(y + 1)

                return out.isoformat() if return_iso else out
    raise ValueError(f"Could not parse relative date description: '{text}'")


def _iter_month_days(y: int, m: int):
    for d in range(1, calendar.monthrange(y, m)[1] + 1):
        yield date(y, m, d)


def _month_ref_to_year_month(text: str, base: date) -> tuple[int, int]:
    """
    Resolve phrases like:
      - 'this month' / 'next month' / 'last month'
      - 'next Jan' / 'Jan' / 'Dec.' (bare month uses upcoming/on-or-after year)
    """
    s = _canon(text)
    # this/next/last month
    m = re.fullmatch(r"(this|next|coming|upcoming|last|previous)\s+month", s)
    if m:
        mod = _normalize_modifier(m.group(1))
        shift = 0 if mod == "this" else (1 if mod in ("next", "coming") else -1)
        dt = date(base.year, base.month, 15)
        dt2 = add_months(dt, shift)
        return dt2.year, dt2.month

    # explicit month (with optional modifier)
    m = re.fullmatch(r"(this|next|coming|upcoming|last|previous)?\s*([a-z.]+)", s)
    if m and m.group(2) in MONTHS:
        mod = _normalize_modifier(m.group(1))
        mm = MONTHS[m.group(2)]
        this = date(base.year, mm, 15)
        if mod in ("next", "coming"):
            return (this.year, mm) if this > base else (this.year + 1, mm)
        if mod == "this":
            return (this.year, mm) if this >= base else (this.year + 1, mm)
        if mod in ("last", "previous"):
            return (this.year, mm) if this < base else (this.year - 1, mm)
        # no modifier → upcoming/on-or-after
        return (this.year, mm) if this >= base else (this.year + 1, mm)

    raise ValueError(f"Could not resolve month reference: '{text}'")


def _collect_weekdays_list(chunk: str) -> set[int] | None:
    s = _canon(chunk)
    if "weekend" in s:
        return {WEEKDAYS["saturday"], WEEKDAYS["sunday"]}
    if "weekday" in s:
        return {WEEKDAYS[d] for d in ("monday", "tuesday", "wednesday", "thursday", "friday")}

    # try comma/and separated weekdays using the original chunk so commas are preserved
    parts = re.split(r"\s*(?:,|and|\&|\+)\s*", chunk)
    out = set()
    for raw in parts:
        p = _canon(raw).strip()
        if not p:
            continue
        # allow trailing 's' ("mondays")
        p = p.rstrip(".")
        p = WEEKDAY_SINGULAR.get(p, p)
        if p in WEEKDAYS:
            out.add(WEEKDAYS[p])
    return out or None


def _expand_md_range(
    y: int, m: int, start_day: int, end_day: int, base: date | None = None, modifier: str = ""
) -> list[date]:
    """
    Expand a month-day range into a list of dates.

    If base and modifier are provided, and modifier is 'next'/'coming', will check if the start
    date has passed and bump to next year if needed. This overrides the year determined by
    _month_ref_to_year_month to ensure "next" ranges work intuitively.
    """
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    last = calendar.monthrange(y, m)[1]
    start_day = max(1, min(start_day, last))
    end_day = max(1, min(end_day, last))

    # If using "next" modifier and the start of the range has passed, bump to next year
    # Check against current year first to handle cases where _month_ref_to_year_month
    # might have returned the current year due to using the 15th as reference
    if base is not None and modifier in ("next", "coming"):
        start_date = date(y, m, start_day)
        # For "next", if start_date <= base, we need to go to next occurrence
        if start_date <= base:
            # If we're already in a future year, that's fine
            # Otherwise bump to next year
            if y <= base.year:
                y += 1
                # Revalidate days for new year (handles leap year edge cases)
                last = calendar.monthrange(y, m)[1]
                start_day = max(1, min(start_day, last))
                end_day = max(1, min(end_day, last))

    return [date(y, m, d) for d in range(start_day, end_day + 1)]


def _expand_span(start: date, end: date, weekday_filter: set[int] | None = None) -> list[date]:
    if end < start:
        start, end = end, start
    res = []
    d = start
    while d <= end:
        if not weekday_filter or d.weekday() in weekday_filter:
            res.append(d)
        d += timedelta(days=1)
    return res


def parse_relative_dates(query: str, base: date | None = None, return_iso: bool = True) -> list[date] | list[str]:
    """
    Parse ranges / multi-dates:
      - "Saturdays and Sundays in this month"
      - "weekends in the next month"
      - "Mondays and Fridays in next Jan through May"
      - "next May 11-14 and May 18-21"
      - "Sat and Sun from next Oct 12 through Nov 25"
      - "next Nov 9th, 16th, 23th, 30th, and Dec 7th"
      - "the first week of the next month"
      - "the second week of next Jan"
    Returns a sorted list of date objects (or ISO strings if return_iso=True).
    """
    if base is None:
        base = date.today()
    s = _canon(query)

    out: list[date] = []

    # ------------------------------------------------------------
    # 0) "the <ordinal> week of (the)? <modifier> month" OR
    #    "the <ordinal> week of <month-ref>"
    #    e.g., "the first week of the next month"
    #          "the second week of next Jan"
    # ------------------------------------------------------------
    m = re.fullmatch(
        r"(?:the\s+)?(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+week\s+of\s+(?:the\s+)?(this|next|coming|upcoming|last|previous)\s+month",
        s,
    )
    if m:
        ordinal_str = m.group(1)
        mod = _normalize_modifier(m.group(2))

        # Parse ordinal (first=1, second=2, etc.)
        ordinal_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
        week_num = ordinal_map.get(ordinal_str)
        is_last = ordinal_str == "last"

        # Get target month
        shift = 0 if mod == "this" else (1 if mod in ("next", "coming") else -1)
        target = add_months(base, shift)
        y, mo = target.year, target.month

        # Find the week boundaries
        if is_last:
            # Last week: find last day of month and work backwards
            last_day = calendar.monthrange(y, mo)[1]
            end_date = date(y, mo, last_day)
            # Last week ends on the last day of month, goes back 6 days
            start_date = date(y, mo, max(1, last_day - 6))
        else:
            # Nth week: starts on day (week_num - 1) * 7 + 1
            start_day = (week_num - 1) * 7 + 1
            end_day = min(start_day + 6, calendar.monthrange(y, mo)[1])
            start_date = date(y, mo, start_day)
            end_date = date(y, mo, end_day)

        out = _expand_span(start_date, end_date, None)
        return [d.isoformat() for d in out] if return_iso else out

    # Also check for "the <ordinal> week of <month-ref>"
    m = re.fullmatch(
        r"(?:the\s+)?(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+week\s+of\s+(?:the\s+)?(.+)",
        s,
    )
    if m:
        ordinal_str = m.group(1)
        month_ref = m.group(2)

        # Parse ordinal
        ordinal_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
        week_num = ordinal_map.get(ordinal_str)
        is_last = ordinal_str == "last"

        # Try to resolve the month reference
        try:
            y, mo = _month_ref_to_year_month(month_ref, base)
        except ValueError:
            # Not a valid month reference, fall through to other patterns
            pass
        else:
            # Find the week boundaries
            if is_last:
                last_day = calendar.monthrange(y, mo)[1]
                end_date = date(y, mo, last_day)
                start_date = date(y, mo, max(1, last_day - 6))
            else:
                start_day = (week_num - 1) * 7 + 1
                end_day = min(start_day + 6, calendar.monthrange(y, mo)[1])
                start_date = date(y, mo, start_day)
                end_date = date(y, mo, end_day)

            out = _expand_span(start_date, end_date, None)
            return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 1) "<weekdays> in (this|next|last) month"
    #    e.g., "Saturdays and Sundays in this month"
    #          "weekends in the next month"
    # ------------------------------------------------------------
    m = re.fullmatch(r"(.+?)\s+in\s+(?:the\s+)?(this|next|coming|upcoming|last|previous)\s+month", s)
    if m:
        wds = _collect_weekdays_list(m.group(1))
        if not wds:
            raise ValueError("Could not parse weekdays in the left-hand side")
        y, mo = _month_ref_to_year_month(m.group(2) + " month", base)
        for d in _iter_month_days(y, mo):
            if d.weekday() in wds:
                out.append(d)
        out.sort()
        return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 2) "<weekdays> in <month-ref> through <month-ref>"
    #    "Mondays and Fridays in next Jan through May"
    # ------------------------------------------------------------
    m = re.fullmatch(r"(.+?)\s+in\s+(.+?)\s+through\s+(.+)", s)
    if m:
        wds = _collect_weekdays_list(m.group(1))
        if not wds:
            raise ValueError("Could not parse weekdays in the left-hand side")
        y1, m1 = _month_ref_to_year_month(m.group(2), base)
        y2, m2 = _month_ref_to_year_month(m.group(3), base=date(y1, m1, 15))  # resolve end relative to start
        # iterate months inclusive
        y, mo = y1, m1
        while (y < y2) or (y == y2 and mo <= m2):
            for d in _iter_month_days(y, mo):
                if d.weekday() in wds:
                    out.append(d)
            # next month
            if mo == 12:
                y, mo = y + 1, 1
            else:
                mo += 1
        out.sort()
        return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 3) "from <date> through <date>" with optional weekday filter in front
    #    "Sat and Sun from next Oct 12 through Nov 25"
    # ------------------------------------------------------------
    m = re.fullmatch(r"(.+?)\s+from\s+(.+?)\s+through\s+(.+)", s)
    if m:
        # left side may be weekdays or the literal start date
        try:
            wds = _collect_weekdays_list(m.group(1)) or set()
            start = parse_relative_date(m.group(2), base, return_iso=False)
            # end resolves relative to start if needed
            end_base = start
            try:
                end = parse_relative_date(m.group(3), end_base, return_iso=False)
            except Exception:
                # fallback: resolve relative to `base`, and if end < start, bump a year
                end = parse_relative_date(m.group(3), base, return_iso=False)
                if end < start:
                    end = date(end.year + 1, end.month, end.day)
        except Exception:
            # maybe there is no weekday filter; treat the entire thing as "from X through Y"
            wds = set()
            start = parse_relative_date(m.group(1), base, return_iso=False)
            end_base = start
            end = parse_relative_date(m.group(2), end_base, return_iso=False)
        out = _expand_span(start, end, wds if wds else None)
        return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 4) "<month-ref> <dd-dd> (and <month-ref> <dd-dd> ...)"
    #    "next May 11-14 and May 18-21"
    # ------------------------------------------------------------
    # First, split by " and "
    chunks = [c.strip() for c in re.split(r"\s+and\s+", s)]
    if len(chunks) > 1:
        context_year, context_month = None, None
        context_modifier = ""
        for ch in chunks:
            # match "[<mod>] <month> <d1>-<d2>"
            m = re.fullmatch(
                r"(?:((?:this|next|coming|upcoming|last|previous)\s+)?([a-z.]+)\s+)?(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?",
                ch,
            )
            if m:
                if m.group(2):  # has month (maybe with modifier)
                    mon_ref = ((m.group(1) or "") + (m.group(2) or "")).strip()
                    y, mo = _month_ref_to_year_month(mon_ref, base)
                    context_year, context_month = y, mo
                    context_modifier = _normalize_modifier(m.group(1))
                elif context_month is None:
                    raise ValueError(f"Month missing in segment: '{ch}'")
                else:
                    y, mo = context_year, context_month
                d1, d2 = int(m.group(3)), int(m.group(4))
                out.extend(_expand_md_range(y, mo, d1, d2, base, context_modifier))
                continue
            # If it's not a range, maybe it's a lone day list handled in block 5; delay return
            break
        else:
            out = sorted(set(out))
            return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 5) Month with multiple days, optionally rolling into another month
    #    Examples:
    #      "next Nov 9th, 16th, 23th, 30th, and Dec 7th"
    #      "Nov 1, 2, 3 and Dec 10"
    #      "upcoming Jan 5th 12th 19th"
    #
    # Strategy: scan tokens L->R, maintain current (year, month).
    # - When we see [mod]? <month> <day>, set context and add that day.
    # - Subsequent bare <day> tokens add to the same month until another month shows up.
    # - A new [mod]? <month> <day> switches context.
    # ------------------------------------------------------------
    def _clean_tok(tok: str) -> str:
        """Strip trailing punctuation from token."""
        return tok.rstrip(",.;")

    def _is_mod(tok: str) -> bool:
        return tok in {"this", "next", "coming", "upcoming", "last", "previous"}

    def _is_month(tok: str) -> bool:
        # Strip trailing punctuation (commas, periods) before checking
        return _clean_tok(tok) in MONTHS

    def _day_from(tok: str) -> int | None:
        # Strip trailing punctuation (commas, periods) before matching
        tok = _clean_tok(tok)
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", tok)
        return int(m.group(1)) if m else None

    tokens = s.split()
    i = 0
    cur_y = cur_m = None
    added_any = False

    while i < len(tokens):
        tok = tokens[i]

        # Case A: [mod]? <month> <day>
        if _is_mod(tok) and i + 2 < len(tokens) and _is_month(tokens[i + 1]) and _day_from(tokens[i + 2]) is not None:
            mon_ref = f"{tok} {_clean_tok(tokens[i + 1])}"
            cur_y, cur_m = _month_ref_to_year_month(mon_ref, base if cur_y is None else date(cur_y, cur_m, 15))
            d = _day_from(tokens[i + 2])
            out.append(date(cur_y, cur_m, d))
            added_any = True
            i += 3
            continue

        # Case B: <month> <day>  (no modifier)
        if _is_month(tok) and i + 1 < len(tokens) and _day_from(tokens[i + 1]) is not None:
            mon_ref = _clean_tok(tokens[i])
            # resolve relative to prior context mid-month if exists, else base
            cur_y, cur_m = _month_ref_to_year_month(mon_ref, base if cur_y is None else date(cur_y, cur_m, 15))
            d = _day_from(tokens[i + 1])
            out.append(date(cur_y, cur_m, d))
            added_any = True
            i += 2
            continue

        # Case C: bare <day> (must have a month context already)
        d = _day_from(tok)
        if d is not None and cur_y is not None and cur_m is not None:
            out.append(date(cur_y, cur_m, d))
            added_any = True
            i += 1
            continue

        # Otherwise skip filler tokens like "and", stray punctuation, etc.
        i += 1

    if added_any:
        out = sorted(set(out))
        return [d.isoformat() for d in out] if return_iso else out

    # ------------------------------------------------------------
    # 6) Fallbacks:
    #    - Single month day range: "<month-ref> <d1>-<d2>"
    #    - Simple "in <month-ref>" with weekday phrase on the left already covered above
    #    - Otherwise, try single-date parser and return [that date]
    # ------------------------------------------------------------
    m = re.fullmatch(
        r"(?:((?:this|next|coming|upcoming|last|previous)\s+)?([a-z.]+)\s+)?(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?",
        s,
    )
    if m:
        modifier = _normalize_modifier(m.group(1))
        if m.group(2):
            y, mo = _month_ref_to_year_month((m.group(1) or "") + m.group(2), base)
        else:
            y, mo = base.year, base.month
        out = _expand_md_range(y, mo, int(m.group(3)), int(m.group(4)), base, modifier)
        return [d.isoformat() for d in out] if return_iso else out

    # final fallback: single date (returns list with one)
    try:
        d = parse_relative_date(query, base, return_iso=False)
        return [d.isoformat()] if return_iso else [d]
    except Exception:
        raise ValueError(f"Could not parse date range/multiple description: '{query}'")


# --------------------------
# Quick examples / tests
# --------------------------
if __name__ == "__main__":
    base = date(2025, 11, 6)  # example base date
    examples = [
        "upcoming Friday",  # -> ['2025-11-07']
        "upcoming Thanksgiving",  # -> ['2025-11-27']
        "on the 26th next month",  # -> 2025-12-26
        "26th next month",  # -> ['2025-12-26']
        "26th of the next month",  # -> ['2025-12-26']
        "15th in 3 months",  # -> 2026-02-15
        "the 3rd next December",  # -> ['2025-12-03']
        "3rd next December",  # -> ['2025-12-03']
        "the 3rd of December next",  # -> ['2025-12-03']
        "the 3rd of December",  # -> ['2025-12-03']
        "next Dec. 3rd",  # -> ['2025-12-03']
        "July 4th",  # -> ['2026-07-04']
        "next Valentine's Day",  # -> ['2026-02-14']
        "the next Valentine's Day",  # -> ['2026-02-14']
        "the next Monday",  # -> ['2025-11-10']
        "next MLK Day",  # -> ['2026-01-19']
        "this Thanksgiving",  # -> ['2025-11-27']
        "last Christmas",  # -> ['2024-12-25']
        "in 2 weeks",  # -> ['2025-11-20']
        "next easter",  # -> ['2026-04-20']
        "Saturdays and Sundays in next month",  # -> ['2025-12-06', '2025-12-07', '2025-12-13', '2025-12-14', '2025-12-20', '2025-12-21', '2025-12-27', '2025-12-28']  # noqa: E501
        "weekends in the next month",  # -> ['2025-12-06', '2025-12-07', '2025-12-13', '2025-12-14', '2025-12-20', '2025-12-21', '2025-12-27', '2025-12-28']  # noqa: E501
        "Mondays and Fridays in next Jan through Mar",  # -> ['2026-01-02', '2026-01-05', '2026-01-09', '2026-01-12', '2026-01-16', '2026-01-19', '2026-01-23', '2026-01-26', '2026-01-30', '2026-02-02', '2026-02-06', '2026-02-09', '2026-02-13', '2026-02-16', '2026-02-20', '2026-02-23', '2026-02-27', '2026-03-02', '2026-03-06', '2026-03-09', '2026-03-13', '2026-03-16', '2026-03-20', '2026-03-23', '2026-03-27', '2026-03-30']  # noqa: E501
        "next May 11-14 and May 18-21",  # -> ['2026-05-11', '2026-05-12', '2026-05-13', '2026-05-14', '2026-05-18', '2026-05-19', '2026-05-20', '2026-05-21']  # noqa: E501
        "Sat and Sun from next Oct 12 through Nov 25",  # -> ['2026-10-17', '2026-10-18', '2026-10-24', '2026-10-25', '2026-10-31', '2026-11-01', '2026-11-07', '2026-11-08', '2026-11-14', '2026-11-15', '2026-11-21', '2026-11-22']  # noqa: E501
        "next Nov 9th, 16th, 23th, 30th, and Dec 7th",  # -> ['2025-11-09', '2025-11-16', '2025-11-23', '2025-11-30', '2025-12-07']  # noqa: E501
    ]
    for ex in examples:
        print(ex, "=>", parse_relative_dates(ex, base))
