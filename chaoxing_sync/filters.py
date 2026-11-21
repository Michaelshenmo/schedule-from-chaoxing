import re


def split_names(value) -> list[str]:
    text = str(value or "")
    return [item.strip() for item in re.split(r"[,，;；\r\n]+", text) if item.strip()]


def normalize_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def is_excluded(name, exclusions) -> bool:
    target = normalize_name(name)
    if not target:
        return False
    return any(normalize_name(item) in target for item in exclusions)


def filter_timeline_slots(server_timeline, exclusions):
    if not isinstance(server_timeline, dict) or not exclusions:
        return server_timeline
    result = {}
    for day, slots in server_timeline.items():
        kept = []
        for slot in slots or []:
            if isinstance(slot, dict) and is_excluded(slot.get("name"), exclusions):
                excluded_slot = dict(slot)
                excluded_slot["excluded"] = True
                kept.append(excluded_slot)
            else:
                kept.append(slot)
        if kept:
            result[day] = kept
    return result
