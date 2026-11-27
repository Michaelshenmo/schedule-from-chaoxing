import hashlib
import re
from pathlib import Path

from .breaks import fill_breaks, merge_day_timelines
from .filters import filter_timeline_slots, is_excluded, split_names
from .mappings import (
    apply_break_rules,
    apply_clock_override,
    apply_timeline_rules,
    match_rule,
    mapping_title,
    normalize_rules,
    rule_weeks,
)
from .weeks import parse_weeks


class TimetableParseError(Exception):
    pass


DEFAULT_PERIOD_TIMES = [
    ("08:00", "08:20"),
    ("08:30", "08:50"),
    ("09:00", "09:20"),
    ("09:30", "09:50"),
    ("14:00", "14:20"),
    ("14:30", "14:50"),
    ("15:00", "15:20"),
    ("15:30", "15:50"),
    ("16:00", "16:20"),
    ("16:30", "16:50"),
    ("17:00", "17:20"),
    ("17:30", "17:50"),
]

DAY_PATTERN = re.compile(r"(?:周|星期)([一二三四五六日天])")
PERIOD_PATTERN = re.compile(r"第\s*(\d+)\s*节")
WEEK_LINE_PATTERN = re.compile(r"^[\d\s,，、;；\-~—至单双周()（）]+$")
DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def _stable_id(prefix: str, *parts) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _pick_color(key: str) -> str:
    palette = [
        "#3F51B5", "#2196F3", "#009688", "#4CAF50", "#FF9800",
        "#E91E63", "#9C27B0", "#795548", "#00BCD4", "#607D8B",
    ]
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return palette[int(digest, 16) % len(palette)]


def _simplified_name(name: str) -> str:
    compact = re.sub(r"\s+", "", name)
    return compact if len(compact) <= 8 else compact[:6]


def _normalize_clock(value):
    match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", str(value or ""))
    if not match:
        return None
    try:
        hour = int(match.group(1))
        minute = int(match.group(2))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _is_week_line(value: str) -> bool:
    text = value.strip()
    return bool(text) and WEEK_LINE_PATTERN.match(text) is not None and any(char.isdigit() for char in text)


def _split_blocks(text: str):
    lines = [line.strip() for line in str(text).replace("\r\n", "\n").splitlines() if line.strip()]
    week_indices = [
        index for index in range(1, len(lines))
        if _is_week_line(lines[index])
    ]
    if week_indices:
        blocks = []
        for position, week_index in enumerate(week_indices):
            start = week_index - 1
            end = week_indices[position + 1] - 1 if position + 1 < len(week_indices) else len(lines)
            block = lines[start:end]
            while len(block) < 5:
                block.append("")
            blocks.append(block)
        return blocks
    if lines and len(lines) % 5 == 0:
        return [lines[offset:offset + 5] for offset in range(0, len(lines), 5)]
    return []


def _find_day_columns(ws):
    day_columns = {}
    header_row = None
    max_row = min(ws.max_row, 30)
    max_col = min(ws.max_column, 20)
    for row in range(1, max_row + 1):
        found = {}
        for col in range(1, max_col + 1):
            value = ws.cell(row=row, column=col).value
            if value is None:
                continue
            match = DAY_PATTERN.search(str(value))
            if match:
                found[col] = DAY_MAP[match.group(1)]
        if len(found) >= 2:
            day_columns = found
            header_row = row
            break
    if not day_columns:
        raise TimetableParseError("未找到星期表头")
    return day_columns, header_row


def _find_period_rows(ws, header_row):
    rows = {}
    for row in range(header_row + 1, ws.max_row + 1):
        for col in range(1, min(ws.max_column, 4) + 1):
            value = ws.cell(row=row, column=col).value
            if value is None:
                continue
            match = PERIOD_PATTERN.search(str(value))
            if match:
                rows[row] = int(match.group(1))
                break
    if not rows:
        next_period = 1
        for row in range(header_row + 1, ws.max_row + 1):
            rows[row] = next_period
            next_period += 1
    return rows


def parse_timetable_xlsx(path) -> dict:
    from openpyxl import load_workbook

    file_path = Path(path)
    if not file_path.is_file():
        raise TimetableParseError(f"课表文件不存在：{file_path}")
    try:
        workbook = load_workbook(file_path, data_only=True)
    except Exception as error:
        raise TimetableParseError(f"无法读取课表文件：{error}") from error
    worksheet = workbook.active
    day_columns, header_row = _find_day_columns(worksheet)
    period_rows = _find_period_rows(worksheet, header_row)
    merged_span = {}
    covered = set()
    for merged in worksheet.merged_cells.ranges:
        merged_span[(merged.min_row, merged.min_col)] = (
            merged.max_row - merged.min_row + 1,
            merged.max_col - merged.min_col + 1,
        )
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                if (row, col) != (merged.min_row, merged.min_col):
                    covered.add((row, col))
    blocks = []
    for row, period_start in period_rows.items():
        for col, day in day_columns.items():
            if (row, col) in covered:
                continue
            span_rows, span_cols = merged_span.get((row, col), (1, 1))
            period_end = period_start
            for candidate_row in range(row, row + span_rows):
                period_end = max(period_end, period_rows.get(candidate_row, period_end))
            for day_col in range(col, col + span_cols):
                if day_col != col and day_col not in day_columns:
                    continue
                cell_day = day_columns.get(day_col, day)
                cell = worksheet.cell(row=row, column=day_col)
                if cell.value is None:
                    continue
                for block in _split_blocks(str(cell.value)):
                    name = block[0].strip()
                    if not name:
                        continue
                    weeks = parse_weeks(block[1], 60)
                    blocks.append({
                        "name": name,
                        "teacher": block[2].strip(),
                        "location": block[3].strip(),
                        "weeks": sorted(weeks),
                        "dayOfWeek": cell_day,
                        "periodStart": period_start,
                        "periodEnd": period_end,
                    })
    max_week = max((max(block["weeks"], default=0) for block in blocks), default=20)
    return {
        "blocks": blocks,
        "period_count": max(period_rows.values(), default=9),
        "max_week": max(max_week, 20),
        "source": str(file_path),
    }


NON_CLASS_PATTERN = re.compile(r"早自修|早读|课间|眼保健操|午休|晚自习|放学|升旗|自习|活动|体锻|课后服务|就餐|午餐|晚餐")


def _slot_period(slot):
    if not isinstance(slot, dict):
        return None
    for key in ("period", "section", "index", "periodIndex", "sectionIndex"):
        value = slot.get(key)
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 1:
            return number
    return None


def _slot_clock_pair(slot):
    start = _normalize_clock(slot.get("start"))
    end = _normalize_clock(slot.get("end"))
    if not start or not end or start >= end:
        return None
    return start, end


def _server_slot_layout(server_slots, period_count):
    """Split server slots into class periods and extra (rest/activity) slots.

    Some servers return explicit ``period`` numbers. Others (such as the control
    system policy) only return an ordered list containing rest slots like
    "早自修" or "课间活动", so class periods are inferred by skipping those names.
    """
    count = max(1, int(period_count or 1))
    slots = []
    for slot in server_slots or []:
        if not isinstance(slot, dict):
            continue
        slots.append((slot, _slot_clock_pair(slot)))
    slots.sort(key=lambda item: (item[1] or ("99:99", "99:99")))

    pairs = [None] * count
    extras = []
    excluded_periods = set()
    has_explicit = any(_slot_period(slot) is not None for slot, _ in slots)

    if has_explicit:
        for slot, pair in slots:
            period = _slot_period(slot)
            if period is None:
                extras.append(slot)
                continue
            if not 1 <= period <= count:
                continue
            if slot.get("excluded"):
                excluded_periods.add(period)
                continue
            if pair and pairs[period - 1] is None:
                pairs[period - 1] = pair
        return pairs, extras, excluded_periods

    index = 0
    for slot, pair in slots:
        name = str(slot.get("name") or "")
        if NON_CLASS_PATTERN.search(name):
            extras.append(slot)
            continue
        index += 1
        if index > count:
            continue
        if slot.get("excluded"):
            excluded_periods.add(index)
            continue
        if pair:
            pairs[index - 1] = pair
    return pairs, extras, excluded_periods


def _server_slot_times(server_slots, period_count):
    pairs, _, _ = _server_slot_layout(server_slots, period_count)
    return pairs


def _server_activity_slots(server_timeline):
    result = []
    if not isinstance(server_timeline, dict):
        return result
    for day, slots in server_timeline.items():
        extras = _server_slot_layout(slots, len(slots or []))[1]
        for slot in extras:
            if slot.get("excluded"):
                continue
            pair = _slot_clock_pair(slot)
            name = str(slot.get("name") or "").strip()
            if not pair or not name:
                continue
            result.append({
                "day": int(day) if str(day).isdigit() and 1 <= int(day) <= 7 else 0,
                "name": name,
                "start": pair[0],
                "end": pair[1],
            })
    return result


def _server_period_excluded(server_timeline, day, begin, end):
    if not isinstance(server_timeline, dict):
        return False
    slots = server_timeline.get(day) or server_timeline.get(0) or []
    _, _, excluded_periods = _server_slot_layout(slots, max(len(slots), end))
    return any(begin <= period <= end for period in excluded_periods)


def _user_timeline_slots(existing_schedule, day, period_count, server_timeline=None):
    slots = []
    using_server = isinstance(server_timeline, dict) and bool(server_timeline)
    if using_server:
        server_slots = server_timeline.get(day) or server_timeline.get(0) or []
        slots = _server_slot_times(server_slots, period_count)
    elif isinstance(existing_schedule, dict):
        candidates = []
        for timeline in existing_schedule.get("days") or []:
            if not isinstance(timeline, dict):
                continue
            timeline_id = str(timeline.get("id") or "")
            if timeline_id.startswith("chaoxing-timeline-"):
                continue
            day_of_week = timeline.get("dayOfWeek")
            days = day_of_week if isinstance(day_of_week, list) else [day_of_week]
            if day not in days:
                continue
            entries = [
                entry for entry in timeline.get("entries") or []
                if isinstance(entry, dict)
                and entry.get("type") != "break"
                and entry.get("startTime")
                and entry.get("endTime")
            ]
            if entries:
                candidates.append(entries)
        if candidates:
            entries = max(candidates, key=len)
            entries = sorted(entries, key=lambda entry: str(entry.get("startTime")))
            for entry in entries:
                start = _normalize_clock(entry.get("startTime"))
                end = _normalize_clock(entry.get("endTime"))
                if start and end and start < end:
                    slots.append((start, end))
    if not using_server:
        while len(slots) < period_count:
            fallback_index = len(slots)
            if fallback_index < len(DEFAULT_PERIOD_TIMES):
                slots.append(DEFAULT_PERIOD_TIMES[fallback_index])
            else:
                slots.append(("00:00", "00:00"))
    else:
        while len(slots) < period_count:
            slots.append(None)
    return slots


def build_xlsx_schedule(
    parsed: dict,
    existing_schedule: dict,
    sync_courses: bool,
    sync_timeline: bool,
    server_timeline=None,
    allow_course_timeline: bool = True,
    exclude_courses: str = "",
    exclude_timeline: str = "",
    break_limit_minutes: int = 45,
    mappings=None,
):
    blocks = parsed.get("blocks") or []
    period_count = int(parsed.get("period_count") or 9)
    max_week = max(1, int(parsed.get("max_week") or 20))
    rules = normalize_rules(mappings)
    course_exclusions = split_names(exclude_courses)
    server_timeline = server_timeline if isinstance(server_timeline, dict) else {}
    raw_timeline = filter_timeline_slots(server_timeline, split_names(exclude_timeline))
    global_timeline = apply_timeline_rules(raw_timeline, rules)
    has_week_rules = any(rule_weeks(rule) for rule in rules)
    week_timeline_cache = {}

    def timeline_for_week(week):
        if not raw_timeline or not has_week_rules:
            return global_timeline
        cached = week_timeline_cache.get(week)
        if cached is None:
            cached = apply_timeline_rules(raw_timeline, rules, week=week)
            week_timeline_cache[week] = cached
        return cached

    fill_timeline = bool(sync_timeline and allow_course_timeline)
    subjects = {}
    subjects_by_key = {}
    timelines = {}
    entry_count = 0
    skipped = 0

    def ensure_subject(name: str, teacher: str, location: str) -> str:
        subject_key = f"{name}\x1f{teacher}"
        existing = subjects_by_key.get(subject_key)
        if existing is not None:
            if location and not existing.get("location"):
                existing["location"] = location
            return existing["id"]
        subject_id = _stable_id("cx", subject_key)
        subject = {
            "id": subject_id,
            "name": name,
            "simplifiedName": _simplified_name(name),
            "teacher": teacher or None,
            "icon": "ic_fluent_book_20_regular",
            "color": _pick_color(subject_key),
            "location": location or None,
            "isLocalClassroom": True,
        }
        subjects[subject_id] = subject
        subjects_by_key[subject_key] = subject
        return subject_id

    def rename_subject(subject_id: str, title: str):
        subject = subjects.get(subject_id)
        if subject is None or not title:
            return
        old_name = subject["name"]
        teacher = str(subject.get("teacher") or "")
        if old_name != title:
            subjects_by_key.pop(f"{old_name}\x1f{teacher}", None)
        subject["name"] = title
        subject["simplifiedName"] = _simplified_name(title)
        subjects_by_key[f"{title}\x1f{teacher}"] = subject

    def timeline_for(day: int, weeks):
        week_values = set()
        for value in weeks or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                week_values.add(number)
        week_values = sorted(week_values) or list(range(1, max_week + 1))
        key = (day, tuple(week_values))
        timeline = timelines.get(key)
        if timeline is None:
            timeline = {
                "id": _stable_id("chaoxing-timeline", day, *week_values),
                "dayOfWeek": [day],
                "weeks": "all" if week_values == list(range(1, max_week + 1)) else week_values,
                "entries": [],
                "_ids": set(),
            }
            timelines[key] = timeline
        return timeline

    for block in blocks:
        if not isinstance(block, dict):
            skipped += 1
            continue
        name = str(block.get("name") or "").strip()
        global_rule = match_rule(name, rules, "course")
        if not name or is_excluded(name, course_exclusions) or (global_rule and global_rule.get("exclude")):
            skipped += 1
            continue
        teacher = str(block.get("teacher") or "").strip()
        location = str(block.get("location") or "").strip()
        if sync_courses:
            base_subject_id = ensure_subject(name, teacher, location)
            if global_rule:
                rename_subject(base_subject_id, global_rule.get("title"))
        if not fill_timeline:
            continue
        day = int(block.get("dayOfWeek") or 0)
        start_period = int(block.get("periodStart") or 0)
        end_period = int(block.get("periodEnd") or start_period)
        block_weeks = []
        for value in block.get("weeks") or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                block_weeks.append(number)
        block_weeks = sorted(set(block_weeks)) or list(range(1, max_week + 1))
        if not (1 <= day <= 7) or start_period < 1 or end_period < start_period:
            skipped += 1
            continue

        groups = {}
        for week in block_weeks:
            rule = match_rule(name, rules, "course", week=week, weekday=day)
            if rule and rule.get("exclude"):
                continue
            week_timeline = timeline_for_week(week) if raw_timeline else None
            if raw_timeline and not week_timeline:
                week_timeline = {day: []}
            slots = _user_timeline_slots(
                existing_schedule,
                day,
                max(period_count, end_period),
                week_timeline,
            )
            if end_period > len(slots):
                continue
            display_name = mapping_title(name, rule)
            subject_id = ensure_subject(display_name, teacher, location) if sync_courses else ""
            specs = []
            if rule and (rule.get("start") or rule.get("end")):
                first_slot = slots[start_period - 1] if start_period - 1 < len(slots) else None
                last_slot = slots[end_period - 1] if end_period - 1 < len(slots) else None
                start_time = rule.get("start") or (first_slot[0] if first_slot else "")
                end_time = rule.get("end") or (last_slot[1] if last_slot else "")
                if start_time and end_time and start_time < end_time:
                    specs.append((start_time, end_time))
            else:
                for period in range(start_period, end_period + 1):
                    slot = slots[period - 1] if period - 1 < len(slots) else None
                    if not slot:
                        continue
                    start_time, end_time = slot
                    if start_time >= end_time:
                        continue
                    specs.append((start_time, end_time))
            if not specs:
                continue
            signature = (subject_id, display_name, tuple(specs))
            group = groups.get(signature)
            if group is None:
                group = {
                    "weeks": [],
                    "subject_id": subject_id,
                    "title": display_name,
                    "specs": specs,
                }
                groups[signature] = group
            group["weeks"].append(week)

        if not groups:
            skipped += 1
            continue
        entry_type = "class" if sync_courses else "activity"
        for group in groups.values():
            group_weeks = sorted(group["weeks"])
            timeline = timeline_for(day, group_weeks)
            group_id = _stable_id(
                "chaoxing-block", day, start_period, end_period,
                group["subject_id"] or group["title"],
            )
            for start_time, end_time in group["specs"]:
                entry_id = _stable_id(
                    "chaoxing-entry", day, start_time, end_time,
                    group["subject_id"] or group["title"], *group_weeks,
                )
                if entry_id in timeline["_ids"]:
                    continue
                timeline["_ids"].add(entry_id)
                entry = {
                    "id": entry_id,
                    "type": entry_type,
                    "startTime": start_time,
                    "endTime": end_time,
                    "_breakGroup": group_id,
                }
                if entry_type == "class":
                    entry["subjectId"] = group["subject_id"]
                else:
                    entry["title"] = group["title"]
                timeline["entries"].append(entry)
                entry_count += 1

    if fill_timeline and global_timeline:
        activity_groups = {}
        for week in range(1, max_week + 1):
            week_timeline = timeline_for_week(week)
            if not week_timeline:
                continue
            for activity in _server_activity_slots(week_timeline):
                activity_days = [activity["day"]] if activity["day"] else list(range(1, 8))
                for activity_day in activity_days:
                    signature = (
                        activity_day,
                        activity["name"],
                        activity["start"],
                        activity["end"],
                    )
                    group = activity_groups.get(signature)
                    if group is None:
                        group = {
                            "weeks": [],
                            "day": activity_day,
                            "activity": activity,
                        }
                        activity_groups[signature] = group
                    group["weeks"].append(week)
        for group in activity_groups.values():
            group_weeks = group["weeks"]
            activity = group["activity"]
            activity_day = group["day"]
            timeline = timeline_for(activity_day, group_weeks)
            entry_id = _stable_id(
                "chaoxing-activity", activity_day, activity["name"],
                activity["start"], activity["end"], *group_weeks,
            )
            if entry_id in timeline["_ids"]:
                continue
            timeline["_ids"].add(entry_id)
            timeline["entries"].append({
                "id": entry_id,
                "type": "activity",
                "startTime": activity["start"],
                "endTime": activity["end"],
                "title": activity["name"],
            })
            entry_count += 1

    for timeline in timelines.values():
        timeline.pop("_ids", None)
        timeline["entries"].sort(key=lambda item: item["startTime"])

    meta = existing_schedule.get("meta") if isinstance(existing_schedule, dict) else None
    if not isinstance(meta, dict):
        meta = {}
    meta_id = str(meta.get("id") or "chaoxing-excel")
    start_date = str(meta.get("startDate") or "2026-09-01")
    if meta_id.startswith("chaoxing-") and start_date in ("", "2026-11-09"):
        start_date = "2026-09-01"
    max_cycle = int(meta.get("maxWeekCycle") or 2)
    day_list = fill_breaks(
        sorted(
            timelines.values(),
            key=lambda item: (item["dayOfWeek"][0], item["entries"][0]["startTime"] if item["entries"] else ""),
        ),
        max_week,
        break_limit_minutes,
    )
    day_list = apply_break_rules(day_list, rules, max_week)
    day_list = merge_day_timelines(day_list, max_week, "chaoxing-timeline")
    entry_count = sum(len(timeline.get("entries") or []) for timeline in day_list)
    schedule = {
        "meta": {
            "id": meta_id if meta_id.startswith("chaoxing-") else "chaoxing-excel",
            "version": 1,
            "maxWeekCycle": max_cycle,
            "startDate": start_date,
        },
        "subjects": sorted(subjects.values(), key=lambda item: item["name"]),
        "days": day_list,
        "overrides": [],
    }
    info = {
        "course_count": len(subjects),
        "entry_count": entry_count,
        "timeline_count": len(day_list),
        "skipped_count": skipped,
        "lesson_count": len(blocks),
        "course_list_count": len(blocks),
        "time_slot_count": period_count,
        "failed_weeks": [],
        "max_week": max_week,
        "sync_courses": bool(sync_courses),
        "sync_timeline": bool(sync_timeline),
        "fill_timeline": bool(fill_timeline),
        "timeline_source": "disabled" if not sync_timeline else ("unavailable" if not allow_course_timeline else ("server" if server_timeline else ("target" if existing_schedule else "fallback"))),
        "timetable_error": "",
        "course_error": "",
        "source": "xlsx",
    }
    return schedule, info
