import hashlib
import json
import re
from datetime import date, datetime, timedelta

from .breaks import fill_breaks
from .filters import filter_timeline_slots, is_excluded, split_names
from .xlsx_timetable import _server_activity_slots, _server_slot_times, _slot_period


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


def _server_period_excluded(server_timeline, day, begin, end):
    slots = server_timeline.get(day) or server_timeline.get(0) or []
    for index, slot in enumerate(slots, start=1):
        if not isinstance(slot, dict) or not slot.get("excluded"):
            continue
        period = _slot_period(slot) or index
        if begin <= period <= end:
            return True
    return False


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
):
    curriculum = data.get("curriculum") or {}
    lessons = data.get("lessons") or []
    courses = data.get("courses") or []
    if not isinstance(curriculum, dict) or not isinstance(lessons, list) or not isinstance(courses, list):
        raise ScheduleBuildError("课表数据格式异常")
    times = parse_time_config(_extract_time_values(curriculum))
    server_timeline = server_timeline if isinstance(server_timeline, dict) else {}
    course_exclusions = split_names(exclude_courses)
    server_timeline = filter_timeline_slots(server_timeline, split_names(exclude_timeline))
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

    if sync_courses:
        for course in courses:
            if not isinstance(course, dict):
                continue
            name = str(course.get("name") or "").strip()
            if not name or is_excluded(name, course_exclusions):
                continue
            ensure_subject(name, str(course.get("teacher") or "").strip(), "")

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
        if is_excluded(name, course_exclusions):
            skipped += 1
            continue
        teacher = str(lesson.get("teacherName") or lesson.get("teacher") or "").strip()
        location = str(lesson.get("location") or lesson.get("classroom") or "").strip()
        subject_id = ensure_subject(name, teacher, location) if sync_courses else ""
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
        entry_type = "class" if sync_courses else "activity"
        lesson_times = None
        if server_timeline:
            begin_number = lesson.get("beginNumber") or lesson.get("startSection") or 0
            length = lesson.get("length") or lesson.get("sectionLength") or 1
            try:
                begin_number = int(begin_number)
                length = int(length)
            except (TypeError, ValueError):
                begin_number, length = 0, 1
            if begin_number >= 1 and _server_period_excluded(
                server_timeline, day, begin_number, begin_number + length - 1,
            ):
                skipped += 1
                continue
            lesson_times = _server_lesson_times(lesson, server_timeline)
            if lesson_times is None:
                lesson_times = _lesson_times(lesson, times)
                if lesson_times is not None:
                    entry_type = "activity"
        else:
            lesson_times = _lesson_times(lesson, times)
        if not (1 <= day <= 7) or not weeks or not lesson_times:
            skipped += 1
            continue
        start, finish = lesson_times
        entry_id = _stable_id("cxe", day, start, finish, subject_id or name, entry_type)
        timeline = timeline_for(day, weeks)
        entry = {
            "id": entry_id,
            "type": entry_type,
            "startTime": start,
            "endTime": finish,
        }
        if entry_type == "class":
            entry["subjectId"] = subject_id
        else:
            entry["title"] = name
        if entry_id in timeline["_ids"]:
            continue
        timeline["_ids"].add(entry_id)
        timeline["entries"].append(entry)
        entry_count += 1

    if fill_timeline and server_timeline:
        for activity in _server_activity_slots(server_timeline):
            activity_days = [activity["day"]] if activity["day"] else list(range(1, 8))
            for activity_day in activity_days:
                timeline = timeline_for(activity_day, ["all"])
                entry_id = _stable_id(
                    "cxe-activity", activity_day, activity["name"],
                    activity["start"], activity["end"],
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
    day_list = fill_breaks(day_list, int(data.get("max_week") or 0), break_limit_minutes)
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
        "max_week": int(data.get("max_week") or 0),
        "sync_courses": bool(sync_courses),
        "sync_timeline": bool(sync_timeline),
        "fill_timeline": bool(fill_timeline),
        "timeline_source": "disabled" if not sync_timeline else ("unavailable" if not allow_course_timeline else ("server" if server_timeline else "api")),
        "timeline_error": str(data.get("timeline_error") or ""),
        "timetable_error": str(data.get("timetable_error") or ""),
        "course_error": str(data.get("course_error") or ""),
    }
    return schedule, info
