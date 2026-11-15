import base64
import time

import requests

from .aes import encrypt_cbc
from .weeks import parse_weeks


LOGIN_PAGE = "https://passport2.chaoxing.com/login?fid=&newversion=true&refer=https%3A%2F%2Fi.chaoxing.com"
LOGIN_ENDPOINT = "https://passport2.chaoxing.com/fanyalogin"
SCHEDULE_PAGE = "https://kb.chaoxing.com/res/pc/curriculum/schedule.html"
LESSONS_ENDPOINT = "https://kb.chaoxing.com/pc/curriculum/getMyLessons"
COURSE_LIST_ENDPOINT = "https://mooc1-api.chaoxing.com/mycourse/backclazzdata?view=json&rss=1"
TRANSFER_KEY = b"u2oh6Vu^HWe4_AES"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class ChaoxingError(Exception):
    pass


class AuthError(ChaoxingError):
    pass


def _encrypt_field(value: str) -> str:
    encrypted = encrypt_cbc(TRANSFER_KEY, TRANSFER_KEY, value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")




class ChaoxingClient:
    def __init__(self, timeout=(10, 25)):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def login(self, username: str, password: str) -> None:
        try:
            self.session.get(LOGIN_PAGE, timeout=self.timeout)
            response = self.session.post(
                LOGIN_ENDPOINT,
                data={
                    "fid": "-1",
                    "uname": _encrypt_field(username),
                    "password": _encrypt_field(password),
                    "refer": "https%3A%2F%2Fi.chaoxing.com",
                    "t": "true",
                    "forbidotherlogin": "0",
                    "validate": "",
                    "doubleFactorLogin": "0",
                    "independentId": "0",
                    "independentNameId": "0",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except ValueError as error:
            raise AuthError("登录响应异常，可能需要验证码或账号受限") from error
        except requests.RequestException as error:
            raise ChaoxingError(f"无法连接超星学习通：{error}") from error
        if not isinstance(data, dict):
            raise AuthError("登录响应异常，可能需要验证码或账号受限")
        if not data.get("status"):
            message = data.get("msg2") or data.get("msg") or "账号或密码错误"
            raise AuthError(str(message))
        if data.get("containTwoFactorLogin"):
            raise AuthError("该账号需要二次验证，暂不支持自动同步")
        try:
            self.session.get(SCHEDULE_PAGE, timeout=self.timeout)
        except requests.RequestException:
            pass

    def _get_json(self, url: str, params=None, headers=None, retries: int = 2):
        last_error = None
        for attempt in range(retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
        raise ChaoxingError(f"请求超星学习通失败：{last_error}") from last_error

    def _get_lessons(self, week=None):
        params = {"curTime": int(time.time() * 1000)}
        if week is not None:
            params["week"] = week
        data = self._get_json(
            LESSONS_ENDPOINT,
            params=params,
            headers={
                "Referer": SCHEDULE_PAGE,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        if not isinstance(data, dict):
            raise ChaoxingError("获取课表失败：响应格式异常")
        if data.get("result") != 1:
            raise ChaoxingError(data.get("msg") or "获取课表失败")
        return data.get("data") or {}

    def fetch_courses(self) -> list:
        data = self._get_json(
            COURSE_LIST_ENDPOINT,
            headers={
                "Referer": "https://i.chaoxing.com/base",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            retries=1,
        )
        if not isinstance(data, dict):
            raise ChaoxingError("获取课程列表失败：响应格式异常")
        courses = {}
        for channel in data.get("channelList") or []:
            if not isinstance(channel, dict):
                continue
            content = channel.get("content") or {}
            course_data = ((content.get("course") or {}).get("data") or [])
            for item in course_data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                teacher = str(item.get("teacherfactor") or "").strip()
                record = courses.get(name)
                if record is None:
                    courses[name] = {
                        "name": name,
                        "teacher": teacher,
                        "courseId": item.get("id"),
                        "classId": content.get("id") or channel.get("key"),
                        "image": item.get("imageurl"),
                    }
                elif teacher and not record.get("teacher"):
                    record["teacher"] = teacher
        return list(courses.values())

    def fetch_all_lessons(self, include_timetable=True, include_courses=True) -> dict:
        courses = []
        course_error = ""
        if include_courses:
            try:
                courses = self.fetch_courses()
            except ChaoxingError as error:
                course_error = str(error)
        curriculum = {}
        lessons = []
        failed_weeks = []
        timetable_error = ""
        max_week = 0
        if include_timetable:
            try:
                first = self._get_lessons()
                curriculum = first.get("curriculum") or {}
                if not isinstance(curriculum, dict) or not curriculum:
                    raise ChaoxingError("未获取到课表信息，请确认账号已加入课程")
                try:
                    max_week = int(curriculum.get("maxWeek") or 20)
                except (TypeError, ValueError):
                    max_week = 20
                max_week = max(1, min(max_week, 60))
                records = {}
                succeeded_weeks = 0
                first_week_error = ""
                for week in range(1, max_week + 1):
                    try:
                        payload = self._get_lessons(week)
                    except ChaoxingError as error:
                        failed_weeks.append(week)
                        if len(failed_weeks) == 1:
                            first_week_error = str(error)
                        continue
                    succeeded_weeks += 1
                    for lesson in payload.get("lessonArray") or []:
                        if not isinstance(lesson, dict):
                            continue
                        key = (
                            lesson.get("lessonConfigUuid") or "",
                            lesson.get("name") or lesson.get("displayCourseName") or lesson.get("courseName"),
                            lesson.get("dayOfWeek") or lesson.get("weekDay"),
                            lesson.get("beginNumber") or lesson.get("startSection"),
                            lesson.get("length") or lesson.get("sectionLength"),
                            lesson.get("location") or lesson.get("classroom"),
                            lesson.get("teacherName") or lesson.get("teacher"),
                        )
                        record = records.get(key)
                        if record is None:
                            record = dict(lesson)
                            record["_weeks"] = set()
                            records[key] = record
                        lesson_weeks = parse_weeks(
                            lesson.get("weeks"),
                            max_week,
                            lesson.get("weekType"),
                        )
                        record["_weeks"].update(lesson_weeks or {week})
                if succeeded_weeks == 0:
                    raise ChaoxingError(first_week_error or "所有周次的课表请求均失败")
                for record in records.values():
                    record["weeks"] = sorted(record.pop("_weeks"))
                    lessons.append(record)
                if not lessons and failed_weeks:
                    timetable_error = first_week_error or "部分周次请求失败"
            except ChaoxingError as error:
                timetable_error = str(error)
                curriculum = {}
                lessons = []
        if not courses and not lessons:
            message = timetable_error or course_error or "未获取到课程或课表数据"
            raise ChaoxingError(message)
        return {
            "curriculum": curriculum,
            "lessons": lessons,
            "courses": courses,
            "max_week": max_week,
            "failed_weeks": failed_weeks,
            "timetable_error": timetable_error,
            "course_error": course_error,
        }
