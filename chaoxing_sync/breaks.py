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


def _entry_signature(entry):
    return (
        str(entry.get("type") or ""),
        str(entry.get("startTime") or ""),
        str(entry.get("endTime") or ""),
        str(entry.get("subjectId") or ""),
        str(entry.get("title") or ""),
    )


def merge_day_timelines(days, max_week, id_prefix="cxd"):
    """Group every day's entries into one timeline per distinct weekly arrangement.

    Classes, activities and breaks for the same day share a single timeline; weeks
    whose arrangements are identical are merged into the same timeline.
    """
    if not isinstance(days, list) or not days:
        return days
    try:
        max_week = max(1, int(max_week or 1))
    except (TypeError, ValueError):
        max_week = 1

    buckets = {}
    for timeline in days:
        if not isinstance(timeline, dict):
            continue
        weeks = _weeks_set(timeline.get("weeks"), max_week)
        if not weeks:
            continue
        entries = []
        for entry in timeline.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item.pop("_breakGroup", None)
            if not item.get("startTime") or not item.get("endTime"):
                continue
            entries.append(item)
        if not entries:
            continue
        for day in _day_list(timeline):
            try:
                number = int(day)
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= 7:
                continue
            week_entries = buckets.setdefault(number, {})
            for week in weeks:
                bucket = week_entries.setdefault(week, {})
                for entry in entries:
                    key = str(entry.get("id") or "")
                    if not key:
                        key = "\x1f".join(_entry_signature(entry))
                    bucket.setdefault(key, entry)

    result = []
    for day in sorted(buckets):
        groups = {}
        for week, entries in sorted(buckets[day].items()):
            if not entries:
                continue
            signature = tuple(sorted(_entry_signature(entry) for entry in entries.values()))
            groups.setdefault(signature, []).append((week, entries))
        for items in groups.values():
            week_values = sorted(week for week, _ in items)
            sample = min(items, key=lambda item: item[0])[1]
            entries = sorted(
                (dict(entry) for entry in sample.values()),
                key=lambda entry: (str(entry.get("startTime") or ""), _entry_signature(entry)),
            )
            weeks_value = "all" if week_values == list(range(1, max_week + 1)) else week_values
            result.append({
                "id": _stable_id(id_prefix, day, *week_values),
                "dayOfWeek": [day],
                "weeks": weeks_value,
                "entries": entries,
            })
    result.sort(key=lambda timeline: (
        timeline["dayOfWeek"][0],
        timeline["entries"][0]["startTime"] if timeline["entries"] else "",
    ))
    return result


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
