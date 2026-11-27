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


def normalize_rule(value):
    if not isinstance(value, dict):
        return None
    target = str(value.get("target") or "both").strip().lower()
    if target not in TARGETS:
        target = "both"
    return {
        "name": str(value.get("name") or "").strip(),
        "target": target,
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


def match_rule(name, rules, kind):
    target = normalize_name(name)
    if not target:
        return None
    for rule in rules or []:
        pattern = normalize_name(rule.get("name"))
        if not pattern:
            continue
        if rule.get("target") not in (kind, "both"):
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


def apply_timeline_rules(server_timeline, rules):
    if not isinstance(server_timeline, dict) or not rules:
        return server_timeline
    result = {}
    for day, slots in server_timeline.items():
        mapped = []
        for slot in slots or []:
            if not isinstance(slot, dict):
                mapped.append(slot)
                continue
            item = dict(slot)
            if not item.get("excluded"):
                rule = match_rule(item.get("name"), rules, "timeline")
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


def apply_break_rules(days, rules):
    if not isinstance(days, list) or not rules:
        return days
    for timeline in days:
        if not isinstance(timeline, dict):
            continue
        entries = []
        for entry in timeline.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("type") != "break":
                entries.append(entry)
                continue
            rule = match_rule(entry.get("title"), rules, "timeline")
            if rule:
                if rule.get("exclude"):
                    continue
                if rule.get("title"):
                    entry["title"] = rule["title"]
                if rule.get("start") or rule.get("end"):
                    entry["startTime"], entry["endTime"] = apply_clock_override(
                        entry.get("startTime"), entry.get("endTime"), rule
                    )
            entries.append(entry)
        timeline["entries"] = entries
    return days


def mapping_title(name, rule):
    if rule and rule.get("title"):
        return rule["title"]
    return name
