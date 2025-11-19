"""Unified utilities for parsing and evaluating dynamic date expressions."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from navi_bench.base import UserMetadata
from navi_bench.relative_dates import parse_relative_dates


_MONTH_STYLE_OPTIONS = {"short", "long"}
_PREFIX_OPTIONS = {"next", "none", "auto"}
_RANGE_OPTIONS = {"endpoints", "all"}
_YEAR_OPTIONS = {"set", "none"}

_DYNAMIC_OFFSET_PATTERN = re.compile(
    r"""
    ^\{\s*
    now\(\)
    \s*\+\s*
    timedelta\(
        \s*(?P<start>-?\d+)\s*
        (?:,\s*(?P<end>-?\d+)\s*)?
    \)
    \s*
    \}
    \s*
    (?:\|\s*(?P<options>.+))?
    \s*$
    """,
    re.VERBOSE,
)


def _ordinal_suffix(value: int) -> str:
    """Return the ordinal suffix for a day number."""
    if 10 <= value % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")


def _format_month_day(d: date, include_month: bool = True, month_style: str = "short", year_style: str = "none") -> str:
    suffix = _ordinal_suffix(d.day)
    if include_month:
        month_fmt = "%b" if month_style == "short" else "%B"
        date_str = f"{d.strftime(month_fmt)} {d.day}{suffix}"
        if year_style == "set":
            date_str += f", {d.year}"
        return date_str
    return f"{d.day}{suffix}"


def _format_placeholder_span(start_date: date, end_date: date, month_style: str, year_style: str = "none") -> str:
    if start_date == end_date:
        return _format_month_day(start_date, month_style=month_style, year_style=year_style)
    same_month = start_date.month == end_date.month and start_date.year == end_date.year
    if same_month:
        # For same month/year ranges, put year at the end if set
        start_str = _format_month_day(start_date, month_style=month_style, year_style="none")
        end_str = _format_month_day(end_date, include_month=False, month_style=month_style, year_style="none")
        if year_style == "set":
            return f"{start_str}-{end_str}, {start_date.year}"
        else:
            return f"{start_str}-{end_str}"
    return (
        f"{_format_month_day(start_date, month_style=month_style, year_style=year_style)}"
        f"-{_format_month_day(end_date, month_style=month_style, year_style=year_style)}"
    )


def _parse_dynamic_options(raw: str | None) -> dict[str, str]:
    """
    Parses the options string into a dictionary of key-value pairs.
    """
    if not raw:
        return {}

    options: dict[str, str] = {}

    for part in raw.split("|"):
        text = part.strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Invalid dynamic placeholder option '{text}'. Expected key=value.")

        key, value = text.split("=", 1)
        options[key.strip().lower()] = value.strip().lower()
    return options


def resolve_placeholder_values(
    text: str,
    base_date: date,
) -> tuple[str, list[str]]:
    """
    Resolve a placeholder description into the user-facing text and ISO dates.

    Supports literal descriptions understood by parse_relative_dates as well as the
    dynamic syntax {now() + timedelta(start, end)} where start/end are inclusive offsets and optional options.

    Options:
        - month: "short" or "long"
        - prefix: "next", "none", or "auto"
        - range: "endpoints" or "all"
        - year: "set" or "none"

    """
    stripped = text.strip()

    # Check for dynamic offset pattern first, fallback to string parsing
    match = _DYNAMIC_OFFSET_PATTERN.fullmatch(stripped)
    if match:
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        if end < start:
            raise ValueError("timedelta end offset cannot be smaller than the start offset")

        options = _parse_dynamic_options(match.group("options"))
        month_style = options.get("month", "short")
        if month_style not in _MONTH_STYLE_OPTIONS:
            raise ValueError("month style must be one of: " + ", ".join(_MONTH_STYLE_OPTIONS))

        range_mode = options.get("range", "all")
        if range_mode not in _RANGE_OPTIONS:
            raise ValueError("range must be one of: " + ", ".join(_RANGE_OPTIONS))

        offsets = range(start, end + 1)
        start_date = base_date + timedelta(days=start)
        end_date = base_date + timedelta(days=end)

        if range_mode == "endpoints":
            iso_dates = [start_date.isoformat(), end_date.isoformat()]
        else:
            iso_dates = [(base_date + timedelta(days=offset)).isoformat() for offset in offsets]

        year_style = options.get("year", "none")
        if year_style not in _YEAR_OPTIONS:
            raise ValueError("year must be one of: " + ", ".join(_YEAR_OPTIONS))

        prefix_mode = options.get("prefix", "auto")
        if prefix_mode not in _PREFIX_OPTIONS:
            raise ValueError("prefix must be one of: " + ", ".join(_PREFIX_OPTIONS))

        if prefix_mode == "none":
            prefix = ""
        elif prefix_mode == "next":
            prefix = "next "
        elif prefix_mode == "auto":
            prefix = "next " if start >= 1 else ""

        description = (
            prefix + _format_placeholder_span(start_date, end_date, month_style=month_style, year_style=year_style)
        ).strip()
        return description, iso_dates

    # Fallback to string parsing
    dates = parse_relative_dates(text, base=base_date, return_iso=True)
    return text, dates


def render_task_statement(task: str, resolved_placeholders: dict[str, tuple[str, list[str]]]) -> str:
    """Render a task statement with resolved placeholder values.
    No fallback values for now.
    """

    result = task
    placeholders = re.findall(r"\{(\w+)\}", task)

    for placeholder in placeholders:
        if placeholder in resolved_placeholders:
            resolved_description, _ = resolved_placeholders[placeholder]
            result = result.replace(f"{{{placeholder}}}", resolved_description)
        else:
            raise ValueError(f"Placeholder '{placeholder}' not found in resolved_placeholders")
    return result


def initialize_user_metadata(
    timezone: str,
    location: str,
    timestamp: int | None = None,
) -> UserMetadata:
    """Initialize the user metadata with the current date and time."""
    timestamp = int(datetime.now().timestamp()) if timestamp is None else timestamp
    user_metadata = UserMetadata(timestamp=timestamp, location=location, timezone=timezone)
    return user_metadata


def initialize_placeholder_map(
    user_metadata: UserMetadata,
    values: dict[str, str],
) -> tuple[dict[str, tuple[str, list[str]]], date]:
    """Initialize the placeholder map with the current date and time."""
    base_date = datetime.fromtimestamp(user_metadata.timestamp, ZoneInfo(user_metadata.timezone)).date()
    today = base_date.isoformat()

    placeholder_map = {}
    for placeholder_key, relative_description in values.items():
        resolved_desc, iso_dates = resolve_placeholder_values(relative_description, base_date)
        iso_dates = [d for d in iso_dates if d >= today]
        placeholder_map[placeholder_key] = (resolved_desc, iso_dates)
    return placeholder_map, base_date
