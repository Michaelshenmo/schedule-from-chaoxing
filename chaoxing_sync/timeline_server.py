import json
import re
from urllib.parse import urlparse

import requests


class TimelineServerError(Exception):
    pass


DAY_TOKENS = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "0": 7,
    "mon": 1, "monday": 1, "tue": 2, "tuesday": 2,
    "wed": 3, "wednesday": 3, "thu": 4, "thursday": 4,
    "fri": 5, "friday": 5, "sat": 6, "saturday": 6,
    "sun": 7, "sunday": 7,
    "周一": 1, "星期二": 2, "周三": 3, "星期四": 4, "周五": 5, "星期六": 6, "周日": 7, "周天": 7,
}

TIME_RANGE_PATTERN = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*[-~—至]\s*(\d{1,2})\s*[:：]\s*(\d{1,2})"
)
CLOCK_PATTERN = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")


def _clock(value):
    match = CLOCK_PATTERN.search(str(value or ""))
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


def _minute_clock(value):
    try:
        minute = int(value)
    except (TypeError, ValueError):
        return None
    if minute < 0 or minute > 1439:
        return None
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _split_time_range(value):
    match = TIME_RANGE_PATTERN.search(str(value or ""))
    if not match:
        return None, None
    start = f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
    end = f"{int(match.group(3)):02d}:{int(match.group(4)):02d}"
    if start >= end:
        return None, None
    return start, end


def _day_number(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in DAY_TOKENS:
        return DAY_TOKENS[text]
    for token, day in DAY_TOKENS.items():
        if token in text:
            return day
    return None


def _parse_slot(value, index):
    if isinstance(value, str):
        start, end = _split_time_range(value)
        if not start:
            start = _clock(value)
            end = None
        if not start or not end:
            return None
        return {"name": f"第{index}节", "start": start, "end": end, "dayOfWeek": None}
    if not isinstance(value, dict):
        return None
    start = value.get("startTime") or value.get("start") or value.get("beginTime") or value.get("begin")
    end = value.get("endTime") or value.get("end") or value.get("finishTime") or value.get("finish")
    range_value = value.get("time") or value.get("timeRange") or value.get("range")
    if range_value and (not start or not end):
        range_start, range_end = _split_time_range(range_value)
        start = start or range_start
        end = end or range_end
    start = _clock(start)
    end = _clock(end)
    if start is None:
        start = _minute_clock(value.get("startMinute"))
    if end is None:
        end = _minute_clock(value.get("endMinute"))
    if not start or not end or start >= end:
        return None
    name = value.get("name") or value.get("title") or value.get("label") or f"第{index}节"
    period = value.get("period") or value.get("section") or value.get("periodIndex")
    try:
        period = int(period) if period is not None else None
    except (TypeError, ValueError):
        period = None
    return {
        "name": str(name),
        "start": start,
        "end": end,
        "period": period,
        "dayOfWeek": _day_number(
            value.get("dayOfWeek")
            or value.get("weekday")
            or value.get("day")
            or value.get("week")
        ),
    }


def _slot_list(values):
    if not isinstance(values, (list, tuple)):
        return []
    slots = []
    for index, value in enumerate(values, 1):
        slot = _parse_slot(value, index)
        if slot:
            slots.append(slot)
    return slots


def _group_by_day(values):
    grouped = {}
    for index, value in enumerate(values, 1):
        slot = _parse_slot(value, index)
        if not slot:
            continue
        day = slot.get("dayOfWeek")
        if day is None:
            continue
        grouped.setdefault(day, []).append(slot)
    return grouped


def _policy_schedule_slots(schedule):
    if not isinstance(schedule, dict):
        return {}
    grouped = {}
    for slot in schedule.get("weeklySlots") or schedule.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        start = _minute_clock(slot.get("startMinute"))
        end = _minute_clock(slot.get("endMinute"))
        if not start or not end or start >= end:
            continue
        name = str(slot.get("name") or slot.get("id") or "节次")
        weekdays = slot.get("weekdays") or slot.get("days") or [0]
        if not isinstance(weekdays, (list, tuple)):
            weekdays = [weekdays]
        for day_value in weekdays:
            day = _day_number(day_value)
            if day is None:
                continue
            grouped.setdefault(day, []).append({
                "name": name,
                "start": start,
                "end": end,
                "period": None,
                "dayOfWeek": day,
            })
    return grouped


def _policy_document_slots(document):
    if not isinstance(document, dict):
        return {}
    schedules = document.get("schedules") or document.get("schedule") or []
    if isinstance(schedules, dict):
        schedules = [schedules]
    if not isinstance(schedules, list):
        return {}
    ordered = sorted(
        [item for item in schedules if isinstance(item, dict)],
        key=lambda item: 0 if item.get("scopeType") == "school" else 1,
    )
    for schedule in ordered:
        grouped = _policy_schedule_slots(schedule)
        if grouped:
            return grouped
    return {}


def _policy_periods(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    return {0: _slot_list(value.get("periods") or [])} if _slot_list(value.get("periods") or []) else {}


def parse_timeline_payload(payload):
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (item.get("name") == "class_periods" or item.get("key") == "class_periods"):
                grouped = _policy_periods(item.get("value"))
                if grouped:
                    return grouped
        slots = _slot_list(data)
        if slots:
            grouped = _group_by_day(data)
            return grouped if grouped else {0: slots}
        raise TimelineServerError("时间线接口未返回可识别的节次时间")
    if not isinstance(data, dict):
        raise TimelineServerError("时间线接口返回格式异常")
    policy = (
        _policy_document_slots(data.get("document"))
        or _policy_schedule_slots(data.get("schedule"))
        or _policy_document_slots(data)
    )
    if policy:
        return policy
    for key in ("days", "week", "weekdays", "schedule", "timetable"):
        value = data.get(key)
        if isinstance(value, dict):
            grouped = {}
            for day_token, items in value.items():
                day = _day_number(day_token)
                if day is None:
                    continue
                slots = _slot_list(items)
                if slots:
                    grouped[day] = slots
            if grouped:
                return grouped
        elif isinstance(value, list):
            slots = _slot_list(value)
            if slots:
                grouped = _group_by_day(value)
                return grouped if grouped else {0: slots}
            grouped = {}
            for item in value:
                if not isinstance(item, dict):
                    continue
                day = _day_number(
                    item.get("dayOfWeek")
                    or item.get("weekday")
                    or item.get("day")
                    or item.get("week")
                )
                if day is None:
                    continue
                slots = _slot_list(
                    item.get("timeSlots")
                    or item.get("periods")
                    or item.get("sections")
                    or item.get("items")
                    or item.get("list")
                    or []
                )
                if slots:
                    grouped[day] = slots
            if grouped:
                return grouped
    for key in ("timeSlots", "periods", "sections", "times", "timeline", "items", "list", "records", "dataList", "courseTimes", "timeList"):
        value = data.get(key)
        if isinstance(value, list):
            slots = _slot_list(value)
            if slots:
                grouped = _group_by_day(value)
                return grouped if grouped else {0: slots}
    if all(_day_number(key) is not None for key in data):
        grouped = {}
        for day_token, items in data.items():
            day = _day_number(day_token)
            slots = _slot_list(items)
            if slots:
                grouped[day] = slots
        if grouped:
            return grouped
    raise TimelineServerError("时间线接口未返回可识别的节次时间")


def fetch_timeline(url, token="", detail=None):
    address = str(url or "").strip()
    if not address:
        raise TimelineServerError("未设置时间线服务器地址")
    if not address.lower().startswith(("http://", "https://")):
        address = "http://" + address
    parsed = urlparse(address)
    base = f"{parsed.scheme}://{parsed.netloc}"
    paths = [
        "/api/public/teaching/timeline",
        "/api/teaching/timeline",
        "/api/timeline",
        "/api/public/timeline",
        "/api/class-periods",
        "/api/policies/class_periods",
        "/api/admin/teaching/draft",
    ]
    candidates = []
    if parsed.path not in ("", "/"):
        candidates.append(address)
    candidates.extend(base + path for path in paths)
    headers = {"Accept": "application/json", "User-Agent": "ClassWidgets2-ChaoxingSync"}
    token = str(token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["access-token"] = token
    errors = []
    for candidate in candidates:
        try:
            response = requests.get(candidate, headers=headers, timeout=(8, 20))
            if detail:
                detail(f"GET {candidate} -> HTTP {response.status_code}")
            if response.status_code == 404:
                errors.append(f"{candidate}: 404")
                continue
            if response.status_code in (401, 403):
                errors.append(f"{candidate}: 需要鉴权")
                continue
            response.raise_for_status()
            payload = response.json()
            grouped = parse_timeline_payload(payload)
            if detail:
                detail(f"时间线接口返回 {len(grouped)} 天，{sum(len(v) for v in grouped.values())} 个节次")
            for day in grouped:
                grouped[day].sort(key=lambda slot: slot["start"])
            return grouped
        except ValueError:
            errors.append(f"{candidate}: 不是 JSON")
        except requests.RequestException as error:
            errors.append(f"{candidate}: {error}")
        except TimelineServerError as error:
            errors.append(f"{candidate}: {error}")
    detail = "；".join(errors[-3:]) if errors else "未知错误"
    raise TimelineServerError(f"无法从时间线服务器获取节次时间：{detail}")
