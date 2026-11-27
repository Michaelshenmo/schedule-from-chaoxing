import json
import re

from .filters import normalize_name

TARGETS = ("course", "timeline", "both")
CLOCK_PATTERN = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{1,2})$")


def _clock(value):
    text = str(value or "").strip()
    match = CLOCK_PATTERN.match(text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "exclude"}


def rule_weeks(rule):
    weeks = set()
    for value in (rule or {}).get("weeks") or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 1:
            weeks.add(number)
    return weeks


def rule_weekdays(rule):
    days = set()
    for value in (rule or {}).get("weekdays") or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 7:
            days.add(number)
    return days


def rules_for_week(rules, week):
    if week is None:
        return [rule for rule in rules or [] if not rule_weeks(rule)]
    return [
        rule
        for rule in rules or []
        if not rule_weeks(rule) or week in rule_weeks(rule)
    ]


def normalize_rule(value):
    if not isinstance(value, dict):
        return None
    target = str(value.get("target") or "both").strip().lower()
    if target not in TARGETS:
        target = "both"
    return {
        "name": str(value.get("name") or "").strip(),
        "target": target,
        "weekdays": sorted(rule_weekdays(value)),
        "weeks": sorted(rule_weeks(value)),
        "start": _clock(value.get("start")),
        "end": _clock(value.get("end")),
        "title": str(value.get("title") or "").strip(),
        "exclude": _as_bool(value.get("exclude")),
    }


def normalize_rules(value):
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    if isinstance(value, dict) and "rules" in value:
        value = value.get("rules")
    if not isinstance(value, list):
        return []
    rules = []
    for item in value:
        rule = normalize_rule(item)
        if rule is not None:
            rules.append(rule)
    return rules


def dump_rules(rules):
    return json.dumps(rules or [], ensure_ascii=False, separators=(",", ":"))


def match_rule(name, rules, kind, week=None, weekday=None):
    target = normalize_name(name)
    if not target:
        return None
    for rule in rules or []:
        pattern = normalize_name(rule.get("name"))
        if not pattern:
            continue
        if rule.get("target") not in (kind, "both"):
            continue
        selected_weeks = rule_weeks(rule)
        if selected_weeks and (week is None or week not in selected_weeks):
            continue
        selected_days = rule_weekdays(rule)
        if selected_days and (weekday is None or weekday not in selected_days):
            continue
        if pattern in target:
            return rule
    return None


def apply_clock_override(start, end, rule):
    if not rule:
        return start, end
    rule_start = rule.get("start") or ""
    rule_end = rule.get("end") or ""
    new_start = rule_start or start
    new_end = rule_end or end
    if not new_start or not new_end or new_start >= new_end:
        return start, end
    return new_start, new_end


def apply_timeline_rules(server_timeline, rules, week=None):
    if not isinstance(server_timeline, dict) or not rules:
        return server_timeline
    result = {}
    for day, slots in server_timeline.items():
        try:
            weekday = int(day)
        except (TypeError, ValueError):
            weekday = None
        if weekday is not None and not 1 <= weekday <= 7:
            weekday = None
        mapped = []
        for slot in slots or []:
            if not isinstance(slot, dict):
                mapped.append(slot)
                continue
            item = dict(slot)
            if not item.get("excluded"):
                rule = match_rule(item.get("name"), rules, "timeline", week=week, weekday=weekday)
                if rule:
                    if rule.get("exclude"):
                        item["excluded"] = True
                    else:
                        if rule.get("title"):
                            item["name"] = rule["title"]
                        if rule.get("start") or rule.get("end"):
                            item["start"], item["end"] = apply_clock_override(
                                item.get("start"), item.get("end"), rule
                            )
            mapped.append(item)
        if mapped:
            result[day] = mapped
    return result


def _timeline_weeks(value, max_week=None):
    try:
        limit = int(max_week or 0)
    except (TypeError, ValueError):
        limit = 0
    if value in (None, "", [], (), "all"):
        if limit >= 1:
            return set(range(1, limit + 1))
        return None
    if isinstance(value, int):
        return {value} if value >= 1 else set()
    result = set()
    if isinstance(value, (list, tuple, set)):
        for item in value:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                result.add(number)
        return result
    for part in re.split(r"[,，;；\s]+", str(value or "")):
        if not part:
            continue
        range_match = re.match(r"^(\d+)\s*[-~—至]\s*(\d+)$", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            result.update(range(min(start, end), max(start, end) + 1))
            continue
        try:
            number = int(part)
        except (TypeError, ValueError):
            continue
        if number >= 1:
            result.add(number)
    return result


def _entry_signature(entry):
    return (
        str(entry.get("type") or ""),
        str(entry.get("startTime") or ""),
        str(entry.get("endTime") or ""),
        str(entry.get("subjectId") or ""),
        str(entry.get("title") or ""),
    )


def _apply_timeline_entry_rule(entry, rules, week, weekday=None):
    if not isinstance(entry, dict):
        return entry
    item = dict(entry)
    if item.get("type") not in ("break", "activity"):
        return item
    rule = match_rule(item.get("title"), rules, "timeline", week=week, weekday=weekday)
    if not rule:
        return item
    if rule.get("exclude"):
        return None
    if rule.get("title"):
        item["title"] = rule["title"]
    if rule.get("start") or rule.get("end"):
        item["startTime"], item["endTime"] = apply_clock_override(
            item.get("startTime"), item.get("endTime"), rule
        )
    return item


def apply_break_rules(days, rules, max_week=None):
    if not isinstance(days, list) or not rules:
        return days
    has_week_rules = any(rule_weeks(rule) for rule in rules)
    result = []
    for timeline in days:
        if not isinstance(timeline, dict):
            result.append(timeline)
            continue
        day_values = timeline.get("dayOfWeek")
        if not isinstance(day_values, (list, tuple)):
            day_values = [day_values] if day_values is not None else []
        weekday = None
        if len(day_values) == 1:
            try:
                number = int(day_values[0])
                if 1 <= number <= 7:
                    weekday = number
            except (TypeError, ValueError):
                weekday = None
        weeks = _timeline_weeks(timeline.get("weeks"), max_week)
        if not has_week_rules or weeks is None:
            entries = []
            for entry in timeline.get("entries") or []:
                transformed = _apply_timeline_entry_rule(entry, rules, None, weekday)
                if transformed is not None:
                    entries.append(transformed)
            if entries:
                item = dict(timeline)
                item["entries"] = entries
                result.append(item)
            continue
        groups = {}
        for week in sorted(weeks):
            entries = []
            for entry in timeline.get("entries") or []:
                transformed = _apply_timeline_entry_rule(entry, rules, week, weekday)
                if transformed is not None:
                    entries.append(transformed)
            signature = tuple(_entry_signature(entry) for entry in entries)
            group = groups.get(signature)
            if group is None:
                group = {"weeks": [], "entries": entries}
                groups[signature] = group
            group["weeks"].append(week)
        for group in groups.values():
            if not group["entries"]:
                continue
            week_values = group["weeks"]
            item = dict(timeline)
            if max_week and week_values == list(range(1, int(max_week) + 1)):
                item["weeks"] = "all"
            else:
                item["weeks"] = week_values
            item["id"] = "{}-{}".format(
                timeline.get("id") or "timeline",
                "-".join(str(week) for week in week_values),
            )
            item["entries"] = [dict(entry) for entry in group["entries"]]
            result.append(item)
    return result


def mapping_title(name, rule):
    if rule and rule.get("title"):
        return rule["title"]
    return name
