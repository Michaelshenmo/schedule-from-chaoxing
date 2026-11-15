import hashlib
import re
from datetime import date, datetime, timedelta


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


def parse_time_config(values):
    result = []
    for value in values or []:
        match = TIME_PATTERN.search(str(value))
        if not match:
            result.append(None)
            continue
        try:
            start = _normalize_time(match.group(1), match.group(2))
            end = _normalize_time(match.group(3), match.group(4))
        except ValueError:
            result.append(None)
            continue
        result.append({"start": start, "end": end})
    return result


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
    try:
        begin = int(lesson.get("beginNumber") or 0)
        length = int(lesson.get("length") or 1)
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


def build_schedule(data: dict, sync_courses: bool, sync_timeline: bool):
    curriculum = data.get("curriculum") or {}
    lessons = data.get("lessons") or []
    if not isinstance(curriculum, dict) or not isinstance(lessons, list):
        raise ScheduleBuildError("课表数据格式异常")
    times = parse_time_config(curriculum.get("lessonTimeConfigArray"))
    if not times or not any(times):
        raise ScheduleBuildError("未获取到节次时间配置，暂时无法生成课表")

    subjects = {}
    timelines = {}
    entry_count = 0
    skipped = 0

    for lesson in lessons:
        if not isinstance(lesson, dict):
            skipped += 1
            continue
        name = str(lesson.get("name") or "").strip()
        try:
            day = int(lesson.get("dayOfWeek") or 0)
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
        lesson_times = _lesson_times(lesson, times)
        if not name or not (1 <= day <= 7) or not weeks or not lesson_times:
            skipped += 1
            continue
        start, finish = lesson_times
        teacher = str(lesson.get("teacherName") or "").strip()
        location = str(lesson.get("location") or "").strip()
        subject_key = f"{name}\x1f{teacher}"
        subject_id = _stable_id("cx", subject_key)
        entry_id = _stable_id("cxe", day, start, finish, subject_id)

        if sync_courses:
            subject = subjects.get(subject_id)
            if subject is None:
                subjects[subject_id] = {
                    "id": subject_id,
                    "name": name,
                    "simplifiedName": _simplified_name(name),
                    "teacher": teacher or None,
                    "icon": "ic_fluent_book_20_regular",
                    "color": _pick_color(subject_key),
                    "location": location or None,
                    "isLocalClassroom": True,
                }
            elif location and not subject.get("location"):
                subject["location"] = location

        if not sync_timeline:
            continue

        timeline_key = (day, tuple(weeks))
        timeline = timelines.get(timeline_key)
        if timeline is None:
            timeline = {
                "id": _stable_id("cxd", day, *weeks),
                "dayOfWeek": [day],
                "weeks": list(weeks),
                "entries": [],
                "_ids": set(),
            }
            timelines[timeline_key] = timeline

        entry = {
            "id": entry_id,
            "type": "class" if sync_courses else "activity",
            "startTime": start,
            "endTime": finish,
        }
        if sync_courses:
            entry["subjectId"] = subject_id
        else:
            entry["title"] = name
        if entry_id in timeline["_ids"]:
            continue
        timeline["_ids"].add(entry_id)
        timeline["entries"].append(entry)
        entry_count += 1

    for timeline in timelines.values():
        timeline.pop("_ids", None)
        timeline["entries"].sort(key=lambda item: item["startTime"])

    subject_list = sorted(subjects.values(), key=lambda item: (item["name"], item.get("teacher") or ""))
    day_list = sorted(
        timelines.values(),
        key=lambda item: (item["dayOfWeek"][0], item["entries"][0]["startTime"] if item["entries"] else ""),
    )
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
        "max_week": int(data.get("max_week") or 0),
    }
    return schedule, info
