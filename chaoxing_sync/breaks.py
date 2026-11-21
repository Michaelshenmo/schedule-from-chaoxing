import hashlib

from .weeks import parse_weeks

DEFAULT_BREAK_LIMIT_MINUTES = 45


def _stable_id(prefix, *parts):
    raw = "\x1f".join(str(part) for part in parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _weeks_set(weeks, max_week):
    if weeks in (None, "", [], "all"):
        return set(range(1, max_week + 1))
    if isinstance(weeks, int):
        return {weeks} if 1 <= weeks <= max_week else set()
    if isinstance(weeks, (list, tuple, set)):
        result = set()
        for value in weeks:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= number <= max_week:
                result.add(number)
        return result
    return parse_weeks(weeks, max_week)


def _minutes(value):
    try:
        hour, minute = str(value).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _day_list(timeline):
    value = timeline.get("dayOfWeek")
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _strip_groups(days):
    for timeline in days:
        if not isinstance(timeline, dict):
            continue
        for entry in timeline.get("entries") or []:
            if isinstance(entry, dict):
                entry.pop("_breakGroup", None)


def fill_breaks(days, max_week, break_limit_minutes=DEFAULT_BREAK_LIMIT_MINUTES):
    if not isinstance(days, list) or not days:
        return days
    try:
        max_week = max(1, int(max_week or 1))
    except (TypeError, ValueError):
        max_week = 1
    try:
        break_limit = int(break_limit_minutes)
    except (TypeError, ValueError):
        break_limit = DEFAULT_BREAK_LIMIT_MINUTES
    if break_limit < 0:
        break_limit = None

    records = []
    observed_weeks = set()
    for timeline in days:
        if not isinstance(timeline, dict):
            continue
        weeks = _weeks_set(timeline.get("weeks"), max_week)
        observed_weeks.update(weeks)
        for day in _day_list(timeline):
            for entry in timeline.get("entries") or []:
                if not isinstance(entry, dict) or entry.get("type") == "break":
                    continue
                start = str(entry.get("startTime") or "")
                end = str(entry.get("endTime") or "")
                if not start or not end or start >= end:
                    continue
                records.append((int(day), start, end, str(entry.get("_breakGroup") or ""), weeks))

    if not records:
        _strip_groups(days)
        return days
    if observed_weeks:
        max_week = max(max_week, max(observed_weeks))

    breaks = {}
    for day in range(1, 8):
        for week in range(1, max_week + 1):
            active = [
                (start, end, group)
                for record_day, start, end, group, weeks in records
                if record_day == day and week in weeks
            ]
            active.sort(key=lambda item: (item[0], item[1]))
            for previous, following in zip(active, active[1:]):
                previous_end = previous[1]
                following_start = following[0]
                if previous_end >= following_start:
                    continue
                if previous[2] and previous[2] == following[2]:
                    continue
                previous_minutes = _minutes(previous_end)
                following_minutes = _minutes(following_start)
                if previous_minutes is None or following_minutes is None:
                    continue
                if break_limit is not None and following_minutes - previous_minutes > break_limit:
                    continue
                key = (day, previous_end, following_start)
                breaks.setdefault(key, set()).add(week)

    for (day, start, end), weeks in breaks.items():
        if not weeks:
            continue
        week_values = sorted(weeks)
        weeks_value = "all" if len(week_values) == max_week else week_values
        days.append({
            "id": _stable_id("cxd-break", day, start, end, *week_values),
            "dayOfWeek": [day],
            "weeks": weeks_value,
            "entries": [{
                "id": _stable_id("cxe-break", day, start, end),
                "type": "break",
                "startTime": start,
                "endTime": end,
                "title": "课间",
            }],
        })
    _strip_groups(days)
    return days
