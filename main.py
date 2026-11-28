import json
import os
import re
import tempfile
from datetime import datetime
from enum import IntEnum
from pathlib import Path

import requests
from loguru import logger
from PySide6.QtCore import Property, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from ClassWidgets.SDK import CW2Plugin, ConfigBaseModel, PluginAPI

try:
    from ClassWidgets.SDK import NotificationLevel
except ImportError:
    class NotificationLevel(IntEnum):
        INFO = 0
        ANNOUNCEMENT = 1
        WARNING = 2
        SYSTEM = 3

from chaoxing_sync.client import ChaoxingClient, ChaoxingError
from chaoxing_sync.credentials import protect_account, unprotect_account
from chaoxing_sync.filters import split_names
from chaoxing_sync.mappings import dump_rules, normalize_rules
from chaoxing_sync.schedule_builder import build_schedule
from chaoxing_sync.admin_device import fetch_admin_timeline
from chaoxing_sync.timeline_server import fetch_timeline
from chaoxing_sync.xlsx_timetable import build_xlsx_schedule, parse_timetable_xlsx


ACTIVE_THREADS = set()
DEFAULT_SCHEDULE_NAME = "超星学习通"
AUTO_TIMELINE_PREFIXES = ("chaoxing-timeline-", "cxd-")
UI_CONFIG_NAME = "config.json"
DEFAULT_SHOW_TIMETABLE_EXPORT = False

CONFIG_ITEMS = [
    {
        "key": "sync_courses",
        "label": "同步课程",
        "group": "同步",
        "description": "写入课程名称、教师和地点",
        "default": True,
        "kind": "bool",
    },
    {
        "key": "sync_timeline",
        "label": "同步时间线",
        "group": "同步",
        "description": "获取并写入课程时间线",
        "default": True,
        "kind": "bool",
    },
    {
        "key": "interval_minutes",
        "label": "自动同步间隔",
        "group": "同步",
        "description": "分钟；-1 表示仅启动和手动同步",
        "default": 20,
        "kind": "int",
    },
    {
        "key": "schedule_name",
        "label": "目标课表名称",
        "group": "课表",
        "description": "同步结果写入的独立课表",
        "default": DEFAULT_SCHEDULE_NAME,
        "kind": "str",
    },
    {
        "key": "timetable_file",
        "label": "课表文件",
        "group": "课表",
        "description": "本地 .xlsx 课表路径",
        "default": "",
        "kind": "str",
    },
    {
        "key": "timetable_url",
        "label": "课表下载地址",
        "group": "课表",
        "description": "课表文件的下载地址",
        "default": "",
        "kind": "str",
    },
    {
        "key": "timeline_server_url",
        "label": "时间线服务器",
        "group": "时间线",
        "description": "集控系统服务器地址",
        "default": "",
        "kind": "str",
    },
    {
        "key": "device_sn",
        "label": "设备 SN",
        "group": "时间线",
        "description": "学校提供的设备 SN",
        "default": "",
        "kind": "str",
    },
    {
        "key": "exclude_courses",
        "label": "排除课程（旧版）",
        "group": "日程映射",
        "description": "旧版排除项，导入后会迁移到日程映射",
        "default": "",
        "kind": "str",
        "legacy": True,
    },
    {
        "key": "exclude_timeline",
        "label": "排除时间线（旧版）",
        "group": "日程映射",
        "description": "旧版排除项，导入后会迁移到日程映射",
        "default": "",
        "kind": "str",
        "legacy": True,
    },
    {
        "key": "schedule_mappings",
        "label": "日程映射规则",
        "group": "日程映射",
        "description": "按名称、星期和周次改写或排除日程",
        "default": [],
        "kind": "mappings",
    },
    {
        "key": "schedule_max_week",
        "label": "最大周次",
        "group": "日程映射",
        "description": "最近同步的周次范围，用于周次选择器",
        "default": 20,
        "kind": "int",
    },
    {
        "key": "break_limit_minutes",
        "label": "课间上限",
        "group": "日程映射",
        "description": "分钟；-1 不限制，0 不填充课间",
        "default": 45,
        "kind": "int",
    },
    {
        "key": "notify_startup",
        "label": "启动同步通知",
        "group": "通知",
        "description": "启动时自动同步完成后通知",
        "default": True,
        "kind": "bool",
    },
    {
        "key": "notify_scheduled",
        "label": "定时同步通知",
        "group": "通知",
        "description": "定时自动同步完成后通知",
        "default": False,
        "kind": "bool",
    },
    {
        "key": "notify_manual",
        "label": "手动同步通知",
        "group": "通知",
        "description": "点击立即同步后通知",
        "default": True,
        "kind": "bool",
    },
    {
        "key": "chaoxing_account",
        "label": "学习通账号与密码",
        "group": "账号",
        "description": "",
        "default": "",
        "kind": "account",
        "exportable": False,
    },
]


class PluginConfig(ConfigBaseModel):
    encrypted_account: str = ""
    sync_courses: bool = True
    sync_timeline: bool = True
    interval_minutes: int = 20
    schedule_name: str = DEFAULT_SCHEDULE_NAME
    timetable_file: str = ""
    timetable_url: str = ""
    timeline_server_url: str = ""
    device_sn: str = ""
    exclude_courses: str = ""
    exclude_timeline: str = ""
    schedule_mappings: str = ""
    schedule_max_week: int = 20
    break_limit_minutes: int = 45
    last_sync_at: str = ""
    notify_startup: bool = True
    notify_scheduled: bool = False
    notify_manual: bool = True


class AccountCheckThread(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, username: str, password: str):
        super().__init__()
        self._username = username
        self._password = password

    def run(self):
        username = self._username
        password = self._password
        try:
            self._username = ""
            self._password = ""
            try:
                ChaoxingClient().login(username, password)
            except Exception as error:
                self.failed.emit(str(error))
                return
            try:
                blob = protect_account(username, password)
            except Exception as error:
                self.failed.emit(f"账号验证成功，但加密保存失败：{error}")
                return
            self.succeeded.emit(blob)
        finally:
            username = ""
            password = ""


class SyncThread(QThread):
    succeeded = Signal(object, str)
    failed = Signal(str, str)

    def __init__(
        self,
        encrypted_account: str,
        sync_courses: bool,
        sync_timeline: bool,
        reason: str,
        timetable_file: str = "",
        timetable_url: str = "",
        timeline_server_url: str = "",
        device_sn: str = "",
        exclude_courses: str = "",
        exclude_timeline: str = "",
        break_limit_minutes: int = 45,
        existing_schedule=None,
        mappings: str = "",
    ):
        super().__init__()
        self._encrypted_account = encrypted_account
        self._sync_courses = sync_courses
        self._sync_timeline = sync_timeline
        self._reason = reason
        self._timetable_file = timetable_file
        self._timetable_url = timetable_url
        self._timeline_server_url = timeline_server_url
        self._device_sn = device_sn
        self._exclude_courses = exclude_courses
        self._exclude_timeline = exclude_timeline
        self._break_limit_minutes = break_limit_minutes
        self._existing_schedule = existing_schedule or {}
        self._mappings = mappings
        self.details = []

    def _detail(self, message: str):
        self.details.append(str(message))

    @property
    def details_text(self) -> str:
        return "\n".join(self.details)

    def _download_timetable(self, url: str) -> Path:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        if self._encrypted_account:
            try:
                username, password = unprotect_account(self._encrypted_account)
                client = ChaoxingClient(detail=self._detail)
                client.login(username, password)
                session = client.session
            except Exception:
                pass
        response = session.get(url, timeout=(10, 90))
        response.raise_for_status()
        content = response.content
        if not content.startswith(b"PK"):
            raise ValueError("下载的文件不是有效的 xlsx 文件")
        descriptor, name = tempfile.mkstemp(prefix="chaoxing-timetable-", suffix=".xlsx")
        os.close(descriptor)
        path = Path(name)
        path.write_bytes(content)
        return path

    def run(self):
        username = ""
        password = ""
        temp_file = None
        admin_timeline = {}
        timeline_error = ""
        admin_configured = bool(self._timeline_server_url)
        self._detail(f"触发方式：{self._reason}")
        if self._exclude_courses.strip():
            self._detail(f"排除课程：{self._exclude_courses.strip()}")
        if self._exclude_timeline.strip():
            self._detail(f"排除时间线：{self._exclude_timeline.strip()}")
        mapping_count = len([rule for rule in normalize_rules(self._mappings) if rule.get("name")])
        if mapping_count:
            self._detail(f"日程映射：{mapping_count} 条")
        self._detail(f"课间上限：{'不限制' if self._break_limit_minutes < 0 else str(self._break_limit_minutes) + ' 分钟'}")
        try:
            if not self._sync_timeline:
                self._detail("同步时间线已关闭，跳过时间线获取")
            elif admin_configured:
                self._detail(f"时间线服务器：{self._timeline_server_url}")
                if self._device_sn:
                    sn = self._device_sn
                    self._detail(f"设备 SN：{sn[:2]}***{sn[-2:] if len(sn) > 4 else ''}")
                try:
                    if self._device_sn:
                        admin_timeline = fetch_admin_timeline(
                            self._timeline_server_url,
                            self._device_sn,
                            self._detail,
                        )
                    else:
                        admin_timeline = fetch_timeline(self._timeline_server_url, detail=self._detail)
                    self._detail(f"时间线获取成功：{sum(len(v) for v in admin_timeline.values())} 个节次")
                except Exception as error:
                    timeline_error = str(error)
                    self._detail(f"时间线获取失败：{error}")
                    logger.warning(f"Timeline server request failed: {error}")
            else:
                self._detail("未设置时间线服务器")
            if self._sync_timeline and admin_configured and not admin_timeline and not timeline_error:
                timeline_error = "时间线服务器未返回可用节次"
                self._detail(timeline_error)
            source_file = self._timetable_file
            if not source_file and self._timetable_url:
                temp_file = self._download_timetable(self._timetable_url)
                source_file = str(temp_file)
            if source_file:
                parsed = parse_timetable_xlsx(source_file)
                admin_failed = self._sync_timeline and admin_configured and not admin_timeline
                schedule, info = build_xlsx_schedule(
                    parsed,
                    self._existing_schedule,
                    self._sync_courses,
                    self._sync_timeline,
                    admin_timeline,
                    allow_course_timeline=not admin_failed,
                    exclude_courses=self._exclude_courses,
                    exclude_timeline=self._exclude_timeline,
                    break_limit_minutes=self._break_limit_minutes,
                    mappings=self._mappings,
                )
                info["synced_at"] = datetime.now().isoformat(timespec="seconds")
                info["timeline_error"] = timeline_error
                info["admin_failed"] = admin_failed
                self._detail(f"课表文件：{source_file}")
                self._detail(f"构建结果：课程 {info.get('course_count', 0)}，时间段 {info.get('entry_count', 0)}，跳过 {info.get('skipped_count', 0)}")
                self._detail(f"时间线来源：{info.get('timeline_source', '')}")
                info["details"] = self.details_text
                logger.info(
                    "Parsed timetable file {} ({} blocks, {} subjects, {} entries)".format(
                        source_file,
                        info.get("lesson_count", 0),
                        info.get("course_count", 0),
                        info.get("entry_count", 0),
                    )
                )
                self.succeeded.emit((schedule, info), self._reason)
                return
            username, password = unprotect_account(self._encrypted_account)
            client = ChaoxingClient(detail=self._detail)
            client.login(username, password)
            try:
                data = client.fetch_issued_courses()
                issued_courses = True
                self._detail(f"课程接口：getIssuedCourseInfo，返回 {len(data.get('lessons') or [])} 条课程记录")
            except ChaoxingError as issued_error:
                self._detail(f"课程接口回退：getMyLessons（{issued_error}）")
                logger.warning(f"Issued course API failed, falling back: {issued_error}")
                data = client.fetch_all_lessons(
                    include_timetable=self._sync_timeline,
                    include_courses=self._sync_courses,
                )
                data["default_timeline"] = {}
                issued_courses = False
            admin_failed = self._sync_timeline and admin_configured and not admin_timeline
            if not self._sync_timeline:
                effective_timeline = {}
            elif admin_configured:
                effective_timeline = admin_timeline
            else:
                effective_timeline = data.get("default_timeline") or {}
            data["timeline_error"] = timeline_error
            schedule, info = build_schedule(
                data,
                self._sync_courses,
                self._sync_timeline,
                effective_timeline,
                allow_course_timeline=not admin_failed,
                exclude_courses=self._exclude_courses,
                exclude_timeline=self._exclude_timeline,
                break_limit_minutes=self._break_limit_minutes,
                mappings=self._mappings,
            )
            info["synced_at"] = datetime.now().isoformat(timespec="seconds")
            info["timeline_error"] = timeline_error
            if not self._sync_timeline:
                info["timeline_source"] = "disabled"
            elif admin_failed:
                info["timeline_source"] = "unavailable"
            elif admin_configured:
                info["timeline_source"] = "server"
            else:
                info["timeline_source"] = "course" if data.get("default_timeline") else "api"
            info["admin_failed"] = admin_failed
            info["issued_courses"] = issued_courses
            self._detail(f"构建结果：课程 {info.get('course_count', 0)}，时间段 {info.get('entry_count', 0)}，跳过 {info.get('skipped_count', 0)}")
            self._detail(f"时间线来源：{info.get('timeline_source', '')}")
            info["details"] = self.details_text
            logger.info(
                "Chaoxing fetched {} lesson groups across {} weeks ({} weeks failed, {} time slots)".format(
                    info.get("lesson_count", 0),
                    info.get("max_week", 0),
                    len(info.get("failed_weeks") or []),
                    info.get("time_slot_count", 0),
                )
            )
            self.succeeded.emit((schedule, info), self._reason)
        except Exception as error:
            self.failed.emit(str(error), self._reason)
        finally:
            username = ""
            password = ""
            self._encrypted_account = ""
            if temp_file is not None:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "show", "shown", "visible", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "hide", "hidden", "disabled"}:
        return False
    return default


def _parse_selected_config_keys(value):
    try:
        data = json.loads(value or "[]")
    except ValueError as error:
        raise ValueError(f"选择项格式不正确：{error}")
    if not isinstance(data, list):
        raise ValueError("选择项格式不正确")
    result = []
    for item in data:
        if not isinstance(item, str):
            continue
        key = item.strip()
        if key and key not in result:
            result.append(key)
    return result


def _sanitize_schedule_name(name: str) -> str:
    value = str(name or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if not value or value.upper() in reserved:
        value = DEFAULT_SCHEDULE_NAME
    return value[:48]


class Plugin(CW2Plugin):
    configChanged = Signal()
    statusChanged = Signal()
    statusDetailsChanged = Signal()
    syncStateChanged = Signal()
    accountStateChanged = Signal()
    configImportFinished = Signal(str)
    configImportAccountFailed = Signal(str)

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.config = PluginConfig()
        self._provider = None
        self._timer = None
        self._sync_thread = None
        self._account_thread = None
        self._syncing = False
        self._account_checking = False
        self._account_verified = False
        self._pending_encrypted_account = ""
        self._pending_config_import = None
        self._pending_import_keys = []
        self._config_import_checking = False
        self._import_check_thread = None
        self._closing = False
        self._status = ""
        self._status_details = "尚无同步记录"
        self._account_message = ""
        self._account_error = False
        self._current_interval = 0
        self._show_timetable_export = DEFAULT_SHOW_TIMETABLE_EXPORT

    def on_load(self):
        super().on_load()
        self.api.config.register_plugin_model(self.pid, self.config)
        self._show_timetable_export = self._load_show_timetable_export()
        self._migrate_exclusions_to_mappings()
        if self.credentialsConfigured:
            self._account_message = "已保存加密账号"
        icon = self.PATH / "icon.png"
        self._provider = self.api.notification.get_provider(
            self.pid,
            "超星学习通",
            icon=icon if icon.exists() else None,
            use_system_notify=False,
        )
        self.api.ui.register_settings_page(
            "qml/settings.qml",
            title="学习通课表同步",
            icon="ic_fluent_calendar_20_regular",
        )
        try:
            self.api.ui.register_shortcut(
                shortcut_id="sync-now",
                name="同步课表",
                icon="ic_fluent_arrow_sync_20_regular",
                action=self.triggerSync,
            )
        except Exception as error:
            logger.warning(f"Failed to register tray shortcut: {error}")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_sync)
        self._apply_config()
        self._refresh_default_status()
        QTimer.singleShot(3000, self._startup_sync)
        logger.info("Chaoxing schedule plugin loaded")

    def on_unload(self):
        self._closing = True
        if self._timer is not None:
            self._timer.stop()
        for worker in (self._sync_thread, self._account_thread, self._import_check_thread):
            if worker is not None and worker.isRunning():
                worker.wait(1500)
        self._sync_thread = None
        self._account_thread = None
        self._import_check_thread = None
        self._pending_config_import = None
        self._pending_import_keys = []
        self._config_import_checking = False
        try:
            self.api.ui.unregister_plugin_shortcuts(self.pid)
        except Exception as error:
            logger.warning(f"Failed to unregister tray shortcuts: {error}")
        logger.info("Chaoxing schedule plugin unloaded")

    @Property(bool, notify=accountStateChanged)
    def credentialsConfigured(self) -> bool:
        return bool(self.config.encrypted_account)

    @Property(bool, notify=configChanged)
    def syncCourses(self) -> bool:
        return bool(self.config.sync_courses)

    @Property(bool, notify=configChanged)
    def syncTimeline(self) -> bool:
        return bool(self.config.sync_timeline)

    @Property(bool, notify=configChanged)
    def autoSyncAllowed(self) -> bool:
        source_ready = (
            self.credentialsConfigured
            or bool(str(self.config.timetable_file or "").strip())
            or bool(str(self.config.timetable_url or "").strip())
        )
        return source_ready and (self.config.sync_courses or self.config.sync_timeline)

    @Property(int, notify=configChanged)
    def intervalMinutes(self) -> int:
        return int(self.config.interval_minutes)

    @Property(str, notify=configChanged)
    def scheduleName(self) -> str:
        return str(self.config.schedule_name)

    @Property(str, notify=configChanged)
    def timetableFile(self) -> str:
        return str(self.config.timetable_file or "")

    @Property(str, notify=configChanged)
    def timetableUrl(self) -> str:
        return str(self.config.timetable_url or "")

    @Property(bool, notify=configChanged)
    def showTimetableExport(self) -> bool:
        return bool(self._show_timetable_export)

    @Property(str, notify=configChanged)
    def timelineServerUrl(self) -> str:
        return str(self.config.timeline_server_url or "")

    @Property(str, notify=configChanged)
    def deviceSn(self) -> str:
        return str(self.config.device_sn or "")

    @Property(str, notify=configChanged)
    def excludeCourses(self) -> str:
        return str(self.config.exclude_courses or "")

    @Property(str, notify=configChanged)
    def excludeTimeline(self) -> str:
        return str(self.config.exclude_timeline or "")

    @Property(str, notify=configChanged)
    def scheduleMappings(self) -> str:
        return str(self.config.schedule_mappings or "")

    @Property(int, notify=configChanged)
    def maxWeek(self) -> int:
        try:
            value = int(self.config.schedule_max_week)
        except (TypeError, ValueError):
            value = 20
        return max(1, min(value, 60))

    @Property(int, notify=configChanged)
    def breakLimitMinutes(self) -> int:
        return int(self.config.break_limit_minutes)

    @Property(bool, notify=syncStateChanged)
    def syncing(self) -> bool:
        return self._syncing

    @Property(bool, notify=accountStateChanged)
    def accountChecking(self) -> bool:
        return self._account_checking

    @Property(bool, notify=accountStateChanged)
    def accountVerified(self) -> bool:
        return self._account_verified

    @Property(bool, notify=accountStateChanged)
    def configImportChecking(self) -> bool:
        return self._config_import_checking

    @Property(str, notify=accountStateChanged)
    def accountMessage(self) -> str:
        return self._account_message

    @Property(bool, notify=accountStateChanged)
    def accountError(self) -> bool:
        return self._account_error

    @Property(bool, notify=configChanged)
    def notifyStartup(self) -> bool:
        return bool(self.config.notify_startup)

    @Property(bool, notify=configChanged)
    def notifyScheduled(self) -> bool:
        return bool(self.config.notify_scheduled)

    @Property(bool, notify=configChanged)
    def notifyManual(self) -> bool:
        return bool(self.config.notify_manual)

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:
        return self._status

    @Property(str, notify=statusDetailsChanged)
    def statusDetails(self) -> str:
        return self._status_details

    @Property(str, notify=configChanged)
    def lastSyncText(self) -> str:
        value = str(self.config.last_sync_at or "")
        if not value:
            return "尚未同步"
        try:
            moment = datetime.fromisoformat(value)
            return f"上次同步：{moment.strftime('%Y-%m-%d %H:%M:%S')}"
        except ValueError:
            return "尚未同步"

    @Slot(str, str, result=bool)
    def verifyAccount(self, username: str, password: str) -> bool:
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            self._account_message = "请输入手机号/超星号和密码"
            self._account_error = True
            self.accountStateChanged.emit()
            self._set_status("请输入手机号/超星号和密码")
            return False
        if self._syncing:
            self._account_message = "正在同步课表，请稍后再验证账号"
            self._account_error = True
            self.accountStateChanged.emit()
            self._set_status("正在同步课表，请稍后再验证账号")
            return False
        if self._account_checking or self._closing:
            return False
        self._account_checking = True
        self._account_verified = False
        self._pending_encrypted_account = ""
        self._account_message = "正在验证账号，请稍候…"
        self._account_error = False
        self.accountStateChanged.emit()
        self._set_status("正在验证账号，请稍候…")
        thread = AccountCheckThread(username, password)
        thread.succeeded.connect(self._on_account_verified)
        thread.failed.connect(self._on_account_failed)
        thread.finished.connect(lambda: ACTIVE_THREADS.discard(thread))
        ACTIVE_THREADS.add(thread)
        self._account_thread = thread
        thread.start()
        return True

    @Slot(result=bool)
    def saveVerifiedAccount(self) -> bool:
        if self._syncing or self._account_checking or self._closing:
            return False
        if not self._account_verified or not self._pending_encrypted_account:
            self._account_message = "请先验证账号"
            self._account_error = True
            self.accountStateChanged.emit()
            self._set_status("请先验证账号")
            return False
        encrypted_account = self._pending_encrypted_account
        self._pending_encrypted_account = ""
        self._account_verified = False
        self.config.encrypted_account = encrypted_account
        self.config.last_sync_at = ""
        self._account_message = "账号验证成功，已加密保存"
        self._account_error = False
        self.accountStateChanged.emit()
        self._apply_config()
        self._save_config()
        self._set_status("账号已保存；稍后将自动同步课表")
        QTimer.singleShot(1000, self._startup_sync)
        logger.info("Chaoxing account saved")
        return True

    @Slot()
    def resetAccountVerification(self):
        if self._account_checking:
            return
        if not self._account_verified and not self._pending_encrypted_account:
            if self._account_error:
                self._account_error = False
                self._account_message = ""
                self.accountStateChanged.emit()
            return
        self._account_verified = False
        self._pending_encrypted_account = ""
        self._account_message = "账号信息已修改，请重新验证"
        self._account_error = False
        self.accountStateChanged.emit()
        self._set_status("账号信息已修改，请重新验证")

    @Slot()
    def clearAccount(self):
        if self._account_checking or self._syncing:
            return
        self.config.encrypted_account = ""
        self._account_verified = False
        self._pending_encrypted_account = ""
        self._account_message = "已清除保存的账号"
        self._account_error = False
        self.accountStateChanged.emit()
        self._apply_config()
        self._save_config()
        self._set_status("已清除保存的账号")

    @Slot(bool)
    def setSyncCourses(self, enabled: bool):
        self.config.sync_courses = bool(enabled)
        self._apply_config()
        self._set_status(self._auto_sync_status())

    @Slot(bool)
    def setSyncTimeline(self, enabled: bool):
        self.config.sync_timeline = bool(enabled)
        self._apply_config()
        self._set_status(self._auto_sync_status())

    @Slot(int)
    def setIntervalMinutes(self, minutes: int):
        value = int(minutes)
        value = -1 if value < 1 else min(value, 1440)
        self.config.interval_minutes = value
        self._apply_config()
        if value < 1:
            self._set_status("定时同步已关闭，仅保留启动同步和手动同步")
        else:
            self._set_status(f"自动同步间隔已设置为 {value} 分钟")

    @Slot(str)
    def setScheduleName(self, name: str):
        if self._syncing:
            self._set_status("正在同步课表，请稍后再修改目标课表名称")
            return
        value = _sanitize_schedule_name(name)
        self.config.schedule_name = value
        self.configChanged.emit()
        self._set_status(f"目标课表名称已设置为「{value}」")

    @Slot(bool)
    def setNotifyStartup(self, enabled: bool):
        self.config.notify_startup = bool(enabled)
        self._save_config()
        self.configChanged.emit()

    @Slot(bool)
    def setNotifyScheduled(self, enabled: bool):
        self.config.notify_scheduled = bool(enabled)
        self._save_config()
        self.configChanged.emit()

    @Slot(bool)
    def setNotifyManual(self, enabled: bool):
        self.config.notify_manual = bool(enabled)
        self._save_config()
        self.configChanged.emit()

    @Slot(result=str)
    def selectTimetableFile(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            QApplication.activeWindow(),
            "选择课表导出文件",
            "",
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return ""
        self.config.timetable_file = path
        self._save_config()
        self.configChanged.emit()
        self._apply_config()
        self._set_status(f"已选择课表文件：{Path(path).name}")
        return path

    @Slot(str)
    def setTimetableUrl(self, url: str):
        self.config.timetable_url = str(url or "").strip()
        self._save_config()
        self.configChanged.emit()
        self._apply_config()
        self._set_status(
            "已设置课表文件下载地址" if self.config.timetable_url
            else "已清除课表文件下载地址"
        )

    @Slot(str)
    def setTimelineServerUrl(self, url: str):
        self.config.timeline_server_url = str(url or "").strip()
        self._save_config()
        self.configChanged.emit()
        self._apply_config()
        self._set_status(
            "已设置时间线服务器地址" if self.config.timeline_server_url
            else "已清除时间线服务器地址"
        )

    @Slot(str)
    def setDeviceSn(self, sn: str):
        self.config.device_sn = str(sn or "").strip()
        self._save_config()
        self.configChanged.emit()

    @Slot(str)
    def setExcludeCourses(self, value: str):
        self.config.exclude_courses = str(value or "").strip()
        self._save_config()
        self.configChanged.emit()

    @Slot(str)
    def setExcludeTimeline(self, value: str):
        self.config.exclude_timeline = str(value or "").strip()
        self._save_config()
        self.configChanged.emit()

    @Slot(str)
    def setScheduleMappings(self, value: str):
        try:
            data = json.loads(value or "[]")
        except ValueError:
            return
        if not isinstance(data, list):
            return
        self.config.schedule_mappings = dump_rules(normalize_rules(data))
        self._save_config()

    @Slot(int)
    def setBreakLimitMinutes(self, minutes: int):
        value = max(-1, min(int(minutes), 1440))
        self.config.break_limit_minutes = value
        self._save_config()
        self.configChanged.emit()

    @Slot()
    def clearTimetableFile(self):
        self.config.timetable_file = ""
        self._save_config()
        self.configChanged.emit()
        self._apply_config()
        self._set_status("已清除课表文件，将使用超星接口同步")

    @Slot()
    def triggerSync(self):
        self._start_sync("manual")

    @Slot(result=str)
    def startExportConfig(self) -> str:
        try:
            items = self._config_export_items()
        except Exception as error:
            logger.warning(f"Failed to prepare config export: {error}")
            return json.dumps(
                {"ok": False, "message": f"准备导出配置失败：{error}"},
                ensure_ascii=False,
            )
        return json.dumps({"ok": True, "items": items}, ensure_ascii=False)

    @Slot(str, result=str)
    def applyExportConfig(self, selected_keys: str) -> str:
        try:
            selected = _parse_selected_config_keys(selected_keys)
        except ValueError as error:
            return f"导出配置失败：{error}"
        exportable = {
            item["key"]
            for item in CONFIG_ITEMS
            if item.get("exportable", True) and item["key"] != "chaoxing_account"
        }
        selected = [key for key in selected if key in exportable]
        if not selected:
            return "未选择要导出的配置项"
        path, _ = QFileDialog.getSaveFileName(
            QApplication.activeWindow(),
            "导出配置",
            str(Path.home() / "chaoxing-sync-config.json"),
            "JSON 配置 (*.json);;所有文件 (*)",
        )
        if not path:
            return ""
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            payload = self._config_export_payload(selected)
            Path(path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as error:
            logger.warning(f"Failed to export config: {error}")
            self._set_status(f"导出配置失败：{error}")
            return f"导出配置失败：{error}"
        self._set_status(f"配置已导出：{Path(path).name}")
        return f"已导出配置到 {path}"

    @Slot(result=str)
    def startImportConfig(self) -> str:
        if self._syncing:
            return json.dumps(
                {"ok": False, "message": "正在同步课表，请稍后再导入配置"},
                ensure_ascii=False,
            )
        path, _ = QFileDialog.getOpenFileName(
            QApplication.activeWindow(),
            "导入配置",
            str(Path.home()),
            "JSON 配置 (*.json);;所有文件 (*)",
        )
        if not path:
            return json.dumps({"ok": False, "cancelled": True}, ensure_ascii=False)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            settings = data.get("settings") if isinstance(data, dict) else None
            if not isinstance(settings, dict):
                settings = data
            if not isinstance(settings, dict):
                raise ValueError("配置文件格式不正确")
            items = self._config_import_items(settings)
            if not any(item["present"] for item in items):
                raise ValueError("配置文件不包含可导入的设置")
        except Exception as error:
            logger.warning(f"Failed to read config: {error}")
            self._set_status(f"导入配置失败：{error}")
            return json.dumps(
                {"ok": False, "message": f"导入配置失败：{error}"},
                ensure_ascii=False,
            )
        self._pending_config_import = {"settings": settings, "path": path}
        return json.dumps(
            {"ok": True, "items": items, "file": path},
            ensure_ascii=False,
        )

    @Slot(str, result=str)
    def confirmImportedConfig(self, selected_keys: str) -> str:
        if self._pending_config_import is None:
            return json.dumps(
                {"status": "error", "message": "请先选择要导入的配置文件"},
                ensure_ascii=False,
            )
        try:
            selected = _parse_selected_config_keys(selected_keys)
        except ValueError as error:
            return json.dumps(
                {"status": "error", "message": f"导入配置失败：{error}"},
                ensure_ascii=False,
            )
        if not selected:
            return json.dumps(
                {"status": "error", "message": "未选择要导入的配置项"},
                ensure_ascii=False,
            )
        settings = self._pending_config_import["settings"]
        account_selected = "chaoxing_account" in selected
        account = str(settings.get("chaoxing_account") or "").strip()
        password = str(settings.get("chaoxing_password") or "")
        if account_selected and account and password:
            self._pending_import_keys = selected
            self._config_import_checking = True
            self.accountStateChanged.emit()
            thread = AccountCheckThread(account, password)
            thread.succeeded.connect(self._on_import_account_verified)
            thread.failed.connect(self._on_import_account_failed)
            thread.finished.connect(lambda: ACTIVE_THREADS.discard(thread))
            ACTIVE_THREADS.add(thread)
            self._import_check_thread = thread
            thread.start()
            return json.dumps(
                {"status": "verifying", "message": "正在验证学习通账号…"},
                ensure_ascii=False,
            )
        legacy = str(settings.get("encrypted_account") or "").strip()
        account_blob = None if (account_selected and legacy) else ""
        result = self._finish_import(selected, account_blob=account_blob)
        return json.dumps(result, ensure_ascii=False)

    @Slot(result=str)
    def importConfigWithoutAccount(self) -> str:
        if self._pending_config_import is None:
            return "请先选择要导入的配置文件"
        selected = [
            key for key in self._pending_import_keys
            if key != "chaoxing_account"
        ]
        if not selected:
            return "没有其他可导入的配置项"
        result = self._finish_import(selected, account_blob="")
        return result.get("message", "")

    def _finish_import(self, selected, account_blob=None, account_verified=False):
        pending = self._pending_config_import
        if pending is None:
            return {"status": "error", "message": "请先选择要导入的配置文件"}
        selected = list(dict.fromkeys(selected or []))
        try:
            self._config_apply_import(pending["settings"], selected, account_blob)
        except Exception as error:
            logger.warning(f"Failed to import config: {error}")
            self._set_status(f"导入配置失败：{error}")
            self._config_import_checking = False
            self._pending_config_import = None
            self._pending_import_keys = []
            self.accountStateChanged.emit()
            return {"status": "error", "message": f"导入配置失败：{error}"}
        account_selected = "chaoxing_account" in selected
        account_imported = bool(self.config.encrypted_account) if account_selected else False
        self._pending_config_import = None
        self._pending_import_keys = []
        self._config_import_checking = False
        self.accountStateChanged.emit()
        self._migrate_exclusions_to_mappings()
        self._save_config()
        self._apply_config()
        self._set_status("配置已导入")
        message = f"已从 {Path(pending['path']).name} 导入选中的配置项"
        if account_selected:
            if account_imported:
                message += (
                    "；账号已通过验证"
                    if account_verified
                    else "；账号已导入，将在下次同步时验证"
                )
            else:
                message += "；已清除保存的账号"
        return {"status": "done", "message": message}

    @Slot(str)
    def _on_import_account_verified(self, encrypted_account: str):
        if self._closing:
            return
        self._import_check_thread = None
        selected = self._pending_import_keys
        result = self._finish_import(
            selected,
            account_blob=encrypted_account,
            account_verified=True,
        )
        self.configImportFinished.emit(result.get("message", ""))

    @Slot(str)
    def _on_import_account_failed(self, message: str):
        if self._closing:
            return
        self._import_check_thread = None
        self._config_import_checking = False
        self.accountStateChanged.emit()
        self.configImportAccountFailed.emit(str(message))

    @Slot()
    def cancelPendingConfigImport(self):
        self._pending_config_import = None
        self._pending_import_keys = []
        self._config_import_checking = False
        self.accountStateChanged.emit()

    @Slot(result=bool)
    def switchToTargetSchedule(self) -> bool:
        name = _sanitize_schedule_name(self.config.schedule_name)
        schedules = self.api.schedulemanagement.list()
        if not any(item.get("name") == name for item in schedules):
            self._set_status(f"目标课表「{name}」不存在，请先同步")
            return False
        if self.api.schedulemanagement.switch(name):
            self._set_status(f"已切换到课表「{name}」")
            return True
        self._set_status(f"切换到课表「{name}」失败")
        return False

    def _set_status(self, text: str):
        if text == self._status:
            return
        self._status = text
        self.statusChanged.emit()

    def _set_status_details(self, text: str):
        text = str(text or "")
        if text == self._status_details:
            return
        self._status_details = text
        self.statusDetailsChanged.emit()

    def _save_config(self):
        try:
            self.api.config.save()
        except Exception as error:
            logger.warning(f"Failed to save plugin config: {error}")

    @staticmethod
    def _config_is_empty(value):
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, dict, set)):
            return len(value) == 0
        return False

    @staticmethod
    def _config_item(key):
        for item in CONFIG_ITEMS:
            if item["key"] == key:
                return item
        return None

    @staticmethod
    def _config_item_payload(item, present, empty, enabled, checked, warning=""):
        return {
            "key": item["key"],
            "label": item["label"],
            "group": item["group"],
            "description": item["description"],
            "present": bool(present),
            "empty": bool(empty),
            "enabled": bool(enabled),
            "checked": bool(checked),
            "warning": str(warning or ""),
        }

    def _config_export_value(self, key):
        if key == "schedule_mappings":
            return normalize_rules(self.config.schedule_mappings)
        return getattr(self.config, key, "")

    def _config_export_items(self):
        items = []
        for item in CONFIG_ITEMS:
            key = item["key"]
            if key == "chaoxing_account":
                items.append(self._config_item_payload(
                    item, present=True, empty=True, enabled=False, checked=False,
                ))
                continue
            value = self._config_export_value(key)
            if item.get("legacy") and self._config_is_empty(value):
                continue
            items.append(self._config_item_payload(
                item, present=True, empty=self._config_is_empty(value),
                enabled=True, checked=True,
            ))
        return items

    def _config_import_items(self, settings):
        items = []
        for item in CONFIG_ITEMS:
            key = item["key"]
            if item.get("legacy") and key not in settings:
                continue
            if key == "chaoxing_account":
                account = str(settings.get("chaoxing_account") or "").strip()
                password = str(settings.get("chaoxing_password") or "")
                legacy_blob = str(settings.get("encrypted_account") or "").strip()
                present = any(
                    name in settings
                    for name in ("chaoxing_account", "chaoxing_password", "encrypted_account")
                )
                empty = not ((account and password) or legacy_blob)
                items.append(self._config_item_payload(
                    item, present=present, empty=empty,
                    enabled=present, checked=present and not empty,
                    warning="勾选后将清除已保存的账号"
                    if present and empty else "",
                ))
                continue
            present = key in settings
            value = settings.get(key)
            empty = present and self._config_is_empty(value)
            items.append(self._config_item_payload(
                item, present=present, empty=empty,
                enabled=present, checked=present and not empty,
                warning="勾选后将恢复默认设置" if present and empty else "",
            ))
        return items

    def _config_export_payload(self, selected_keys):
        selected = set(selected_keys or [])
        settings = {}
        for item in CONFIG_ITEMS:
            key = item["key"]
            if key == "chaoxing_account" or not item.get("exportable", True):
                continue
            if key in selected:
                settings[key] = self._config_export_value(key)
        # Keep the account schema explicit, but never write credentials.
        settings["chaoxing_account"] = ""
        settings["chaoxing_password"] = ""
        return {
            "format": "class-widgets-2.chaoxing-sync.config",
            "version": 1,
            "plugin_id": self.pid,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "settings": settings,
        }

    def _config_prepare_value(self, key, value):
        item = self._config_item(key)
        if item is None:
            raise ValueError(f"未知配置项：{key}")
        kind = item.get("kind")
        if key == "schedule_name":
            return _sanitize_schedule_name(str(value or ""))
        if key == "schedule_mappings":
            raw = value
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw or "[]")
                except ValueError as error:
                    raise ValueError(f"日程映射格式不正确：{error}")
            if not isinstance(raw, list):
                raise ValueError("日程映射格式不正确")
            return dump_rules(normalize_rules(raw))
        if kind == "bool":
            return _coerce_bool(value, bool(item.get("default")))
        if kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{item['label']}格式不正确")
            if key == "interval_minutes":
                number = max(-1, min(number, 1440))
            elif key == "break_limit_minutes":
                number = max(-1, min(number, 1440))
            elif key == "schedule_max_week":
                number = max(1, min(number, 60))
            return number
        return str(value or "").strip()

    def _config_prepare_account(self, settings):
        account = str(settings.get("chaoxing_account") or "").strip()
        password = str(settings.get("chaoxing_password") or "")
        if account and password:
            try:
                return protect_account(account, password)
            except Exception as error:
                raise ValueError(f"账号加密失败：{error}")
        return str(settings.get("encrypted_account") or "").strip()

    def _config_apply_import(self, settings, selected_keys, account_blob=None):
        selected = list(dict.fromkeys(selected_keys or []))
        updates = {}
        account_selected = "chaoxing_account" in selected
        for key in selected:
            if key == "chaoxing_account":
                if account_blob is None:
                    account_blob = self._config_prepare_account(settings)
                continue
            item = self._config_item(key)
            if item is None or key not in settings:
                continue
            value = settings[key]
            if self._config_is_empty(value):
                value = item.get("default")
            updates[key] = self._config_prepare_value(key, value)
        if account_selected:
            self.config.encrypted_account = account_blob or ""
            self.config.last_sync_at = ""
            self._account_message = (
                "已导入学习通账号"
                if account_blob else "已清除保存的账号"
            )
            self._account_error = False
            self.accountStateChanged.emit()
        for key, value in updates.items():
            setattr(self.config, key, value)

    def _load_show_timetable_export(self) -> bool:
        path = self.PATH / UI_CONFIG_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DEFAULT_SHOW_TIMETABLE_EXPORT
        if not isinstance(data, dict):
            return DEFAULT_SHOW_TIMETABLE_EXPORT
        return _coerce_bool(data.get("show_timetable_export"), DEFAULT_SHOW_TIMETABLE_EXPORT)

    def _migrate_exclusions_to_mappings(self):
        migrated = []
        for name in split_names(self.config.exclude_courses):
            migrated.append({
                "name": name,
                "target": "course",
                "start": "",
                "end": "",
                "title": "",
                "exclude": True,
            })
        for name in split_names(self.config.exclude_timeline):
            migrated.append({
                "name": name,
                "target": "timeline",
                "start": "",
                "end": "",
                "title": "",
                "exclude": True,
            })
        if not migrated:
            return
        rules = [rule for rule in normalize_rules(self.config.schedule_mappings) if rule.get("name")]
        self.config.schedule_mappings = dump_rules(normalize_rules(migrated + rules))
        self.config.exclude_courses = ""
        self.config.exclude_timeline = ""
        self._save_config()
        logger.info(f"Migrated {len(migrated)} exclusion rule(s) to schedule mappings")

    @Slot(result=bool)
    def reloadUiConfig(self) -> bool:
        value = self._load_show_timetable_export()
        if value != self._show_timetable_export:
            self._show_timetable_export = value
            self.configChanged.emit()
        return value

    def _auto_sync_status(self):
        if not (self.config.sync_courses or self.config.sync_timeline):
            return "同步课程与时间线均已关闭，自动同步已停止"
        if self.intervalMinutes < 1:
            return "定时同步已关闭，仅保留启动同步和手动同步"
        return "自动同步已开启"

    def _refresh_default_status(self):
        if not self.credentialsConfigured:
            self._set_status("请先设置超星学习通账号")
        elif not (self.config.sync_courses or self.config.sync_timeline):
            self._set_status("已配置账号，但同步课程与时间线均已关闭")
        elif self.intervalMinutes < 1:
            self._set_status("定时同步已关闭，仅保留启动同步和手动同步")
        else:
            self._set_status(f"自动同步已开启，间隔 {self.intervalMinutes} 分钟")

    def _apply_config(self):
        if self._timer is not None:
            minutes = int(self.config.interval_minutes)
            enabled = self.autoSyncAllowed and minutes >= 1
            if minutes >= 1:
                interval = min(minutes, 1440) * 60 * 1000
                if interval != self._current_interval:
                    self._current_interval = interval
                    self._timer.setInterval(interval)
            if enabled:
                if not self._timer.isActive():
                    self._timer.start()
            else:
                self._timer.stop()
        self.configChanged.emit()

    def _startup_sync(self):
        if self._closing or self._syncing or not self.autoSyncAllowed:
            return
        self._start_sync("startup")

    def _auto_sync(self):
        if self._closing or not self.autoSyncAllowed or self.intervalMinutes < 1:
            return
        self._start_sync("scheduled")

    def _start_sync(self, reason: str):
        manual = reason == "manual"
        if self._closing or self._syncing or self._account_checking:
            return
        timetable_file = str(self.config.timetable_file or "").strip()
        timetable_url = str(self.config.timetable_url or "").strip()
        if not timetable_file and not timetable_url and not self.credentialsConfigured:
            self._set_status("请先设置超星学习通账号、课表文件或课表下载地址")
            if manual:
                self._notify("manual", NotificationLevel.WARNING, "超星学习通", "请先设置账号、课表文件或下载地址")
            return
        if not (self.config.sync_courses or self.config.sync_timeline):
            self._set_status("同步课程与时间线均已关闭，自动同步已停止")
            if manual:
                self._notify("manual", NotificationLevel.WARNING, "超星学习通", "请至少开启同步课程或同步时间线")
            return
        self._syncing = True
        self.syncStateChanged.emit()
        self._set_status("正在同步课表…")
        self._set_status_details("正在同步，请稍候…")
        existing_schedule = (
            self._read_existing_schedule()
            if (timetable_file or timetable_url)
            else None
        )
        thread = SyncThread(
            self.config.encrypted_account,
            bool(self.config.sync_courses),
            bool(self.config.sync_timeline),
            reason,
            timetable_file,
            timetable_url,
            str(self.config.timeline_server_url or "").strip(),
            str(self.config.device_sn or "").strip(),
            str(self.config.exclude_courses or ""),
            str(self.config.exclude_timeline or ""),
            int(self.config.break_limit_minutes),
            existing_schedule,
            str(self.config.schedule_mappings or ""),
        )
        thread.succeeded.connect(self._on_sync_success)
        thread.failed.connect(self._on_sync_failed)
        thread.finished.connect(lambda: ACTIVE_THREADS.discard(thread))
        ACTIVE_THREADS.add(thread)
        self._sync_thread = thread
        thread.start()

    @Slot(str)
    def _on_account_verified(self, encrypted_account: str):
        if self._closing:
            return
        self._account_checking = False
        self._account_verified = True
        self._pending_encrypted_account = encrypted_account
        self._account_message = "验证成功，请点击保存账号"
        self._account_error = False
        self.accountStateChanged.emit()
        self._set_status("账号验证成功，请点击保存账号")
        logger.info("Chaoxing account verified, waiting for save")

    @Slot(str)
    def _on_account_failed(self, message: str):
        if self._closing:
            return
        self._account_checking = False
        self._account_verified = False
        self._pending_encrypted_account = ""
        self._account_message = f"账号验证失败：{message}"
        self._account_error = True
        self.accountStateChanged.emit()
        self._set_status(f"账号验证失败：{message}")
        logger.warning(f"Chaoxing account validation failed: {message}")

    @Slot(object, str)
    def _on_sync_success(self, result, reason: str):
        if self._closing:
            return
        schedule, info = result
        detail = (
            f"课程列表 {info.get('course_list_count', 0)} 条，"
            f"课表记录 {info.get('lesson_count', 0)} 条，"
            f"{info.get('time_slot_count', 0)} 个节次时间"
        )
        failed_weeks = info.get("failed_weeks") or []
        if failed_weeks:
            detail += f"，{len(failed_weeks)} 个周次请求失败"
        requested_courses = bool(info.get("sync_courses"))
        requested_timeline = bool(info.get("sync_timeline"))
        fill_timeline = bool(info.get("fill_timeline", requested_timeline))
        has_courses = bool(info.get("course_count"))
        has_timeline = bool(info.get("entry_count"))
        if requested_courses and requested_timeline and not has_courses and not has_timeline:
            self._on_sync_failed(f"未获取到课程或上课时间，已保留原课表（{detail}）", reason)
            return
        if requested_courses and not requested_timeline and not has_courses:
            self._on_sync_failed(f"未获取到课程数据，已保留原课表（{detail}）", reason)
            return
        if requested_timeline and not requested_courses and not has_timeline:
            self._on_sync_failed(f"未获取到可用的上课时间，已保留原课表（{detail}）", reason)
            return
        timeline_error = str(info.get("timeline_error") or "")
        partial = (
            (requested_courses and not has_courses)
            or (requested_timeline and not has_timeline)
            or bool(timeline_error)
        )
        try:
            name, path, is_current = self._write_schedule(
                schedule,
                requested_courses,
                fill_timeline,
            )
        except Exception as error:
            self._on_sync_failed(f"保存课表失败：{error}", reason)
            return
        self.config.last_sync_at = info.get("synced_at") or datetime.now().isoformat(timespec="seconds")
        try:
            synced_max_week = int(info.get("max_week") or 0)
        except (TypeError, ValueError):
            synced_max_week = 0
        if synced_max_week >= 1:
            self.config.schedule_max_week = max(1, min(synced_max_week, 60))
        self._save_config()
        self._syncing = False
        self.syncStateChanged.emit()
        self._apply_config()
        message = (
            f"已更新课表「{name}」：{info.get('course_count', 0)} 门课程，"
            f"{info.get('entry_count', 0)} 个时间段"
        )
        if info.get("skipped_count"):
            message += f"，跳过 {info['skipped_count']} 条无效记录"
        if requested_timeline and not has_timeline and not info.get("admin_failed"):
            if info.get("source") == "xlsx":
                message += "；目标课表没有可用时间线，已仅同步课程"
            else:
                message += "；超星课表接口未返回上课时间，已仅同步课程"
        if requested_courses and not has_courses:
            message += "；未获取到课程列表"
        source_label = {
            "server": "晋元学生平板终端集控系统",
            "course": "超星课程接口",
            "target": "目标课表",
            "fallback": "内置节次时间",
            "api": "超星课表接口",
            "unavailable": "未同步",
            "disabled": "已关闭",
        }.get(str(info.get("timeline_source") or ""))
        if source_label:
            message += f"；时间线来源：{source_label}"
        if timeline_error:
            message += "；时间线服务器请求失败，已仅同步课程设置"
        if failed_weeks:
            message += f"；{len(failed_weeks)} 个周次获取失败"
        if not is_current:
            message += "；可在课表设置中切换到该课表"
        self._set_status(message)
        details = str(info.get("details") or "").strip()
        self._set_status_details((details + "\n\n" + message).strip() if details else message)
        self._sync_thread = None
        logger.info(f"Chaoxing schedule synced to {path}")
        suffix = "（部分）" if partial else ""
        title = {
            "startup": f"超星学习通启动同步完成{suffix}",
            "scheduled": f"超星学习通定时同步完成{suffix}",
            "manual": f"超星学习通同步完成{suffix}",
        }.get(reason, f"超星学习通同步完成{suffix}")
        level = NotificationLevel.WARNING if partial else NotificationLevel.INFO
        self._notify(reason, level, title, message)

    @Slot(str, str)
    def _on_sync_failed(self, message: str, reason: str):
        if self._closing:
            return
        self._syncing = False
        self.syncStateChanged.emit()
        self._set_status(f"同步失败：{message}")
        thread = self._sync_thread
        details = thread.details_text.strip() if thread is not None else ""
        self._set_status_details((details + "\n\n错误：" + message).strip() if details else message)
        self._sync_thread = None
        logger.warning(f"Chaoxing schedule sync failed: {message}")
        title = {
            "startup": "超星学习通启动同步失败",
            "scheduled": "超星学习通定时同步失败",
            "manual": "超星学习通同步失败",
        }.get(reason, "超星学习通同步失败")
        self._notify(reason, NotificationLevel.WARNING, title, message)

    def _read_existing_schedule(self):
        name = _sanitize_schedule_name(self.config.schedule_name)
        schedules = self.api.schedulemanagement.list()
        entry = next((item for item in schedules if item.get("name") == name), None)
        if entry is None:
            return None
        path = Path(entry["path"])
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if not str(meta.get("id") or "").startswith("chaoxing-"):
            return None
        return data

    @staticmethod
    def _is_auto_timeline(timeline):
        return str((timeline or {}).get("id") or "").startswith(AUTO_TIMELINE_PREFIXES)

    def _merge_schedule(self, existing, generated, sync_courses, fill_timeline):
        existing = existing if isinstance(existing, dict) else {}
        generated = generated if isinstance(generated, dict) else {}
        existing_days = [day for day in existing.get("days") or [] if isinstance(day, dict)]
        existing_subjects = [subject for subject in existing.get("subjects") or [] if isinstance(subject, dict)]
        existing_overrides = [override for override in existing.get("overrides") or [] if isinstance(override, dict)]
        generated_days = [day for day in generated.get("days") or [] if isinstance(day, dict)]
        generated_subjects = [subject for subject in generated.get("subjects") or [] if isinstance(subject, dict)]
        has_user_timeline = any(not self._is_auto_timeline(day) for day in existing_days)
        existing_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else None
        generated_meta = generated.get("meta") if isinstance(generated.get("meta"), dict) else None
        if existing_meta and has_user_timeline:
            meta = dict(existing_meta)
        else:
            meta = dict(generated_meta or existing_meta or {})
            if existing_meta and str(existing_meta.get("id") or "").startswith("chaoxing-"):
                meta["id"] = str(existing_meta.get("id"))
            if not str(meta.get("id") or "").startswith("chaoxing-"):
                meta["id"] = "chaoxing-schedule"
        meta.setdefault("version", 1)
        meta.setdefault("maxWeekCycle", 2)
        meta.setdefault("startDate", "2026-09-01")

        remap = {}
        if sync_courses:
            referenced = set()
            kept_existing_days = (
                existing_days
                if not fill_timeline
                else [day for day in existing_days if not self._is_auto_timeline(day)]
            )
            for day in kept_existing_days:
                for entry in day.get("entries") or []:
                    if isinstance(entry, dict) and entry.get("subjectId"):
                        referenced.add(str(entry["subjectId"]))
            for override in existing_overrides:
                if override.get("subjectId"):
                    referenced.add(str(override["subjectId"]))
            subjects = []
            by_key = {}
            by_name = {}
            for subject in existing_subjects:
                subject_id = str(subject.get("id") or "")
                if subject_id.startswith("cx-") and subject_id not in referenced:
                    continue
                item = dict(subject)
                subjects.append(item)
                name = str(item.get("name") or "").strip()
                teacher = str(item.get("teacher") or "").strip()
                by_key[(name, teacher)] = item
                by_name.setdefault(name, item)
            for subject in generated_subjects:
                name = str(subject.get("name") or "").strip()
                if not name:
                    continue
                teacher = str(subject.get("teacher") or "").strip()
                key = (name, teacher)
                match = by_key.get(key)
                if match is None:
                    same_name = by_name.get(name)
                    if same_name is not None and (not teacher or not str(same_name.get("teacher") or "").strip()):
                        match = same_name
                if match is not None:
                    if teacher and not match.get("teacher"):
                        match["teacher"] = teacher
                    if subject.get("location") and not match.get("location"):
                        match["location"] = subject["location"]
                    remap[str(subject.get("id") or "")] = str(match.get("id") or "")
                else:
                    item = dict(subject)
                    subjects.append(item)
                    by_key[key] = item
                    by_name.setdefault(name, item)
                    remap[str(subject.get("id") or "")] = str(item.get("id") or "")
        else:
            subjects = [dict(subject) for subject in existing_subjects]

        new_days = []
        for day in generated_days:
            item = dict(day)
            entries = []
            for entry in item.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                entry = dict(entry)
                subject_id = entry.get("subjectId")
                if subject_id is not None:
                    entry["subjectId"] = remap.get(str(subject_id), subject_id)
                entries.append(entry)
            item["entries"] = entries
            new_days.append(item)

        if fill_timeline:
            days = [dict(day) for day in existing_days if not self._is_auto_timeline(day)] + new_days
        else:
            days = [dict(day) for day in existing_days]

        return {
            "meta": meta,
            "subjects": subjects,
            "days": days,
            "overrides": [dict(override) for override in existing_overrides],
        }

    def _write_schedule(self, schedule, sync_courses, fill_timeline):
        name = _sanitize_schedule_name(self.config.schedule_name)
        if name != self.config.schedule_name:
            self.config.schedule_name = name
            self.configChanged.emit()
        schedules = self.api.schedulemanagement.list()
        entry = next((item for item in schedules if item.get("name") == name), None)
        created = False
        if entry is None:
            if not self.api.schedulemanagement.add(name):
                schedules = self.api.schedulemanagement.list()
                entry = next((item for item in schedules if item.get("name") == name), None)
            else:
                created = True
                schedules = self.api.schedulemanagement.list()
                entry = next((item for item in schedules if item.get("name") == name), None)
        if entry is None:
            raise RuntimeError("无法创建目标课表")
        path = Path(entry["path"])
        existing = None
        if path.exists() and not created:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                marker = str((existing.get("meta") or {}).get("id") or "")
            except Exception:
                marker = ""
                existing = None
            if not marker.startswith("chaoxing-"):
                raise RuntimeError("目标课表已存在且不是本插件创建的，请更换课表名称")
        merged = self._merge_schedule(existing, schedule, sync_courses, fill_timeline)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(merged, file, ensure_ascii=False, indent=4)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        current = str(getattr(self.api.globalconfig.configs.schedule, "current_schedule", ""))
        is_current = current == name
        if is_current:
            self.api.schedule.reload()
        return name, path, is_current

    def _notify(self, reason: str, level, title: str, message: str):
        if self._provider is None:
            return
        enabled = {
            "startup": self.config.notify_startup,
            "scheduled": self.config.notify_scheduled,
            "manual": self.config.notify_manual,
        }.get(reason, True)
        if not enabled:
            return
        try:
            self._provider.push(level, title, message, 5000, True)
        except Exception as error:
            logger.warning(f"Failed to push notification: {error}")
