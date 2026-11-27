import hashlib
import json
import re
from datetime import date, datetime, timedelta

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
from .xlsx_timetable import (
    _server_activity_slots,
    _server_period_excluded,
    _server_slot_times,
)


class ScheduleBuildError(Exception):
    pass


COLOR_PALETTE = [
    "#3F51B5",
    "#2196F3",
    "#009688",
    "#4CAF50",
    "#FF9800",
    "#E91E63",
    "#9C27B0",
    "#795548",
    "#00BCD4",
    "#607D8B",
]

TIME_PATTERN = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*[-~—～至]\s*(\d{1,2})\s*[:：]\s*(\d{1,2})"
)


def _stable_id(prefix: str, *parts) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _pick_color(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return COLOR_PALETTE[int(digest, 16) % len(COLOR_PALETTE)]


def _normalize_time(hour, minute) -> str:
    hour = int(hour)
    minute = int(minute)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("invalid time")
    return f"{hour:02d}:{minute:02d}"


def _parse_clock(value):
    if value is None:
        return None
    match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", str(value))
    if not match:
        return None
    try:
        return _normalize_time(match.group(1), match.group(2))
    except ValueError:
        return None


def _parse_time_value(value):
    if value is None:
        return None
    if isinstance(value, dict):
        start = value.get("startTime") or value.get("start") or value.get("beginTime")
        end = value.get("endTime") or value.get("end") or value.get("finishTime")
        if start and end:
            start_time = _parse_clock(start)
            end_time = _parse_clock(end)
            if start_time and end_time:
                return start_time, end_time
        return None
    match = TIME_PATTERN.search(str(value))
    if match:
        try:
            return _normalize_time(match.group(1), match.group(2)), _normalize_time(match.group(3), match.group(4))
        except ValueError:
            return None
    single = _parse_clock(value)
    return single, single if single else None


def parse_time_config(values):
    result = []
    for value in values or []:
        parsed = _parse_time_value(value)
        if isinstance(parsed, tuple) and len(parsed) == 2 and parsed[0] and parsed[1]:
            result.append({"start": parsed[0], "end": parsed[1]})
        else:
            result.append(None)
    return result


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        if any(key in value for key in ("startTime", "start", "beginTime", "endTime", "end")):
            return [value]
        ordered_keys = sorted(value, key=lambda key: int(key) if str(key).isdigit() else str(key))
        return [value[key] for key in ordered_keys]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith(("[", "{")):
            try:
                return _as_list(json.loads(text))
            except (TypeError, ValueError):
                pass
        for separator in ("，", ",", "；", ";"):
            if separator in text:
                return [part.strip() for part in text.split(separator) if part.strip()]
        return [text]
    return []


def _extract_time_values(curriculum):
    values = _as_list(curriculum.get("lessonTimeConfigArray"))
    if values:
        return values
    campus_configs = _as_list(curriculum.get("campusLessonTimeConfigArray"))
    best = []
    for item in campus_configs:
        if not isinstance(item, dict):
            continue
        candidate = _as_list(item.get("timeConfig") or item.get("lessonTimeConfigArray"))
        if len(candidate) > len(best):
            best = candidate
    if best:
        return best
    for key in ("sectionTime", "timeConfig"):
        values = _as_list(curriculum.get(key))
        if values:
            return values
    values = []
    for key in ("earlyMorningTime", "morningTime", "afternoonTime", "eveningTime"):
        values.extend(_as_list(curriculum.get(key)))
    return values


def _start_date(curriculum: dict) -> str:
    timestamp = curriculum.get("firstWeekDateReal") or curriculum.get("firstWeekDate")
    if timestamp:
        try:
            value = float(timestamp)
            if value > 100_000_000_000:
                value /= 1000
            start = datetime.fromtimestamp(value).date()
            return (start - timedelta(days=start.weekday())).isoformat()
        except (OSError, OverflowError, TypeError, ValueError):
            text = str(timestamp)
            match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
            if match:
                try:
                    start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    return (start - timedelta(days=start.weekday())).isoformat()
                except ValueError:
                    pass
    try:
        current_week = int(curriculum.get("realCurrentWeek") or curriculum.get("currentWeek") or 1)
    except (TypeError, ValueError):
        current_week = 1
    start = date.today() - timedelta(weeks=max(0, current_week - 1))
    return (start - timedelta(days=start.weekday())).isoformat()


def _simplified_name(name: str) -> str:
    compact = re.sub(r"\s+", "", name)
    if len(compact) <= 8:
        return compact
    return compact[:6]


def _lesson_times(lesson: dict, times):
    explicit_pairs = (
        ("startTime", "endTime"),
        ("lessonBeginTime", "lessonEndTime"),
        ("beginTime", "finishTime"),
    )
    for start_key, end_key in explicit_pairs:
        start = _parse_clock(lesson.get(start_key))
        finish = _parse_clock(lesson.get(end_key))
        if start and finish and start < finish:
            return start, finish
    try:
        begin = int(lesson.get("beginNumber") or lesson.get("startSection") or 0)
        length = int(lesson.get("length") or lesson.get("sectionLength") or 1)
    except (TypeError, ValueError):
        return None
    if begin < 1 or length < 1:
        return None
    end = begin + length - 1
    if end > len(times):
        return None
    start_slot = times[begin - 1]
    end_slot = times[end - 1]
    if not start_slot or not end_slot:
        return None
    start = start_slot["start"]
    finish = end_slot["end"]
    if start >= finish:
        return None
    return start, finish


def _server_lesson_times(lesson: dict, server_timeline):
    try:
        day = int(lesson.get("dayOfWeek") or lesson.get("weekDay") or 0)
        begin = int(lesson.get("beginNumber") or lesson.get("startSection") or 0)
        length = int(lesson.get("length") or lesson.get("sectionLength") or 1)
    except (TypeError, ValueError):
        return None
    if not (1 <= day <= 7) or begin < 1 or length < 1:
        return None
    end = begin + length - 1
    slots = server_timeline.get(day) or server_timeline.get(0) or []
    period_times = _server_slot_times(slots, max(9, end))
    if end > len(period_times):
        return None
    start_slot = period_times[begin - 1]
    end_slot = period_times[end - 1]
    if not start_slot or not end_slot:
        return None
    start = start_slot[0]
    finish = end_slot[1]
    if start >= finish:
        return None
    return start, finish


def build_schedule(
    data: dict,
    sync_courses: bool,
    sync_timeline: bool,
    server_timeline=None,
    allow_course_timeline: bool = True,
    exclude_courses: str = "",
    exclude_timeline: str = "",
    break_limit_minutes: int = 45,
    mappings=None,
):
    curriculum = data.get("curriculum") or {}
    lessons = data.get("lessons") or []
    courses = data.get("courses") or []
    if not isinstance(curriculum, dict) or not isinstance(lessons, list) or not isinstance(courses, list):
        raise ScheduleBuildError("课表数据格式异常")
    times = parse_time_config(_extract_time_values(curriculum))
    rules = normalize_rules(mappings)
    max_week = max(1, int(data.get("max_week") or 0))
    server_timeline = server_timeline if isinstance(server_timeline, dict) else {}
    course_exclusions = split_names(exclude_courses)
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
    subjects_by_name = {}
    timelines = {}
    entry_count = 0
    skipped = 0

    def ensure_subject(name: str, teacher: str, location: str) -> str:
        existing = subjects_by_name.get(name)
        if existing is not None:
            if teacher and not existing.get("teacher"):
                existing["teacher"] = teacher
            if location and not existing.get("location"):
                existing["location"] = location
            return existing["id"]
        subject_key = f"{name}\x1f{teacher}"
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
        subjects_by_name[name] = subject
        return subject_id

    def timeline_for(day: int, weeks):
        weeks_value = "all" if weeks == ["all"] else list(weeks)
        key = (day, tuple(weeks) if weeks != ["all"] else ("all",))
        timeline = timelines.get(key)
        if timeline is None:
            timeline = {
                "id": _stable_id("cxd", day, *key[1]),
                "dayOfWeek": [day],
                "weeks": weeks_value,
                "entries": [],
                "_ids": set(),
            }
            timelines[key] = timeline
        return timeline

    def rename_subject(subject_id: str, title: str):
        subject = subjects.get(subject_id)
        if subject is None or not title:
            return
        old_name = subject["name"]
        if old_name != title:
            subjects_by_name.pop(old_name, None)
        subject["name"] = title
        subject["simplifiedName"] = _simplified_name(title)
        subjects_by_name[title] = subject

    if sync_courses:
        for course in courses:
            if not isinstance(course, dict):
                continue
            name = str(course.get("name") or "").strip()
            rule = match_rule(name, rules, "course")
            if not name or is_excluded(name, course_exclusions) or (rule and rule.get("exclude")):
                continue
            subject_id = ensure_subject(name, str(course.get("teacher") or "").strip(), "")
            if rule:
                rename_subject(subject_id, rule.get("title"))

    for lesson in lessons:
        if not isinstance(lesson, dict):
            skipped += 1
            continue
        name = str(
            lesson.get("name")
            or lesson.get("displayCourseName")
            or lesson.get("courseName")
            or ""
        ).strip()
        if not name:
            skipped += 1
            continue
        global_rule = match_rule(name, rules, "course")
        if is_excluded(name, course_exclusions) or (global_rule and global_rule.get("exclude")):
            skipped += 1
            continue
        teacher = str(lesson.get("teacherName") or lesson.get("teacher") or "").strip()
        location = str(lesson.get("location") or lesson.get("classroom") or "").strip()
        if sync_courses:
            base_subject_id = ensure_subject(name, teacher, location)
            if global_rule:
                rename_subject(base_subject_id, global_rule.get("title"))
        if not fill_timeline:
            continue
        try:
            day = int(lesson.get("dayOfWeek") or lesson.get("weekDay") or 0)
        except (TypeError, ValueError):
            day = 0
        week_values = set()
        for week in lesson.get("weeks") or []:
            try:
                number = int(week)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                week_values.add(number)
        weeks = sorted(week_values)
        if not (1 <= day <= 7) or not weeks:
            skipped += 1
            continue
        begin_number = lesson.get("beginNumber") or lesson.get("startSection") or 0
        length = lesson.get("length") or lesson.get("sectionLength") or 1
        try:
            begin_number = int(begin_number)
            length = int(length)
        except (TypeError, ValueError):
            begin_number, length = 0, 1

        groups = {}
        for week in weeks:
            rule = match_rule(name, rules, "course", week=week, weekday=day)
            if rule and rule.get("exclude"):
                continue
            entry_type = "class" if sync_courses else "activity"
            lesson_times = None
            if global_timeline:
                week_timeline = timeline_for_week(week)
                if not week_timeline:
                    week_timeline = {day: []}
                if begin_number >= 1 and _server_period_excluded(
                    week_timeline, day, begin_number, begin_number + length - 1,
                ):
                    continue
                lesson_times = _server_lesson_times(lesson, week_timeline)
                if lesson_times is None:
                    lesson_times = _lesson_times(lesson, times)
                    if lesson_times is not None:
                        entry_type = "activity"
            else:
                lesson_times = _lesson_times(lesson, times)
            if lesson_times is None and rule and rule.get("start") and rule.get("end"):
                if rule["start"] < rule["end"]:
                    lesson_times = (rule["start"], rule["end"])
            if lesson_times is None:
                continue
            start, finish = apply_clock_override(lesson_times[0], lesson_times[1], rule)
            display_name = mapping_title(name, rule)
            subject_id = ensure_subject(display_name, teacher, location) if sync_courses else ""
            signature = (entry_type, subject_id or display_name, start, finish)
            group = groups.get(signature)
            if group is None:
                group = {
                    "weeks": [],
                    "type": entry_type,
                    "subject_id": subject_id,
                    "title": display_name,
                    "start": start,
                    "finish": finish,
                }
                groups[signature] = group
            group["weeks"].append(week)

        if not groups:
            skipped += 1
            continue
        for group in groups.values():
            group_weeks = group["weeks"]
            entry_id = _stable_id(
                "cxe", day, group["start"], group["finish"],
                group["subject_id"] or group["title"], group["type"], *group_weeks,
            )
            timeline = timeline_for(day, group_weeks)
            if entry_id in timeline["_ids"]:
                continue
            entry = {
                "id": entry_id,
                "type": group["type"],
                "startTime": group["start"],
                "endTime": group["finish"],
            }
            if group["type"] == "class":
                entry["subjectId"] = group["subject_id"]
            else:
                entry["title"] = group["title"]
            timeline["_ids"].add(entry_id)
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
                "cxe-activity", activity_day, activity["name"],
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

    subject_list = sorted(subjects.values(), key=lambda item: (item["name"], item.get("teacher") or ""))
    day_list = sorted(
        timelines.values(),
        key=lambda item: (item["dayOfWeek"][0], item["entries"][0]["startTime"] if item["entries"] else ""),
    )
    max_week = max(1, max_week)
    day_list = fill_breaks(day_list, max_week, break_limit_minutes)
    day_list = apply_break_rules(day_list, rules, max_week)
    day_list = merge_day_timelines(day_list, max_week, "cxd")
    entry_count = sum(len(timeline.get("entries") or []) for timeline in day_list)
    uuid = str(curriculum.get("uuid") or "").strip()
    meta_id = f"chaoxing-{uuid}" if uuid else "chaoxing-schedule"
    schedule = {
        "meta": {
            "id": meta_id,
            "version": 1,
            "maxWeekCycle": 2,
            "startDate": _start_date(curriculum),
        },
        "subjects": subject_list,
        "days": day_list,
        "overrides": [],
    }
    info = {
        "course_count": len(subject_list),
        "entry_count": entry_count,
        "timeline_count": len(day_list),
        "skipped_count": skipped,
        "lesson_count": len(lessons),
        "course_list_count": len(courses),
        "time_slot_count": len([slot for slot in times if slot]),
        "failed_weeks": list(data.get("failed_weeks") or []),
        "max_week": max_week,
        "sync_courses": bool(sync_courses),
        "sync_timeline": bool(sync_timeline),
        "fill_timeline": bool(fill_timeline),
        "timeline_source": "disabled" if not sync_timeline else ("unavailable" if not allow_course_timeline else ("server" if server_timeline else "api")),
        "timeline_error": str(data.get("timeline_error") or ""),
        "timetable_error": str(data.get("timetable_error") or ""),
        "course_error": str(data.get("course_error") or ""),
    }
    return schedule, info
