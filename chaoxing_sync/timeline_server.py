import re

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


def parse_timeline_payload(payload):
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, list):
        slots = _slot_list(data)
        if slots:
            grouped = _group_by_day(data)
            return grouped if grouped else {0: slots}
        raise TimelineServerError("时间线接口未返回可识别的节次时间")
    if not isinstance(data, dict):
        raise TimelineServerError("时间线接口返回格式异常")
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


def fetch_timeline(url, token=""):
    address = str(url or "").strip()
    if not address:
        raise TimelineServerError("未设置时间线服务器地址")
    if not address.lower().startswith(("http://", "https://")):
        address = "https://" + address
    headers = {"Accept": "application/json", "User-Agent": "ClassWidgets2-ChaoxingSync"}
    token = str(token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["access-token"] = token
    try:
        response = requests.get(address, headers=headers, timeout=(10, 30))
        response.raise_for_status()
        payload = response.json()
    except ValueError as error:
        raise TimelineServerError("时间线接口返回的不是 JSON") from error
    except requests.RequestException as error:
        raise TimelineServerError(f"请求时间线服务器失败：{error}") from error
    grouped = parse_timeline_payload(payload)
    for day in grouped:
        grouped[day].sort(key=lambda slot: slot["start"])
    return grouped
