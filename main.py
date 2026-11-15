import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from enum import IntEnum

from loguru import logger
from PySide6.QtCore import Property, QThread, QTimer, Signal, Slot

from ClassWidgets.SDK import CW2Plugin, ConfigBaseModel, PluginAPI

try:
    from ClassWidgets.SDK import NotificationLevel
except ImportError:
    class NotificationLevel(IntEnum):
        INFO = 0
        ANNOUNCEMENT = 1
        WARNING = 2
        SYSTEM = 3

from chaoxing_sync.client import ChaoxingClient
from chaoxing_sync.credentials import protect_account, unprotect_account
from chaoxing_sync.schedule_builder import build_schedule


ACTIVE_THREADS = set()
DEFAULT_SCHEDULE_NAME = "超星学习通"


class PluginConfig(ConfigBaseModel):
    encrypted_account: str = ""
    sync_courses: bool = True
    sync_timeline: bool = True
    interval_minutes: int = 20
    schedule_name: str = DEFAULT_SCHEDULE_NAME
    last_sync_at: str = ""


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
    succeeded = Signal(object, bool)
    failed = Signal(str, bool)

    def __init__(self, encrypted_account: str, sync_courses: bool, sync_timeline: bool, manual: bool):
        super().__init__()
        self._encrypted_account = encrypted_account
        self._sync_courses = sync_courses
        self._sync_timeline = sync_timeline
        self._manual = manual

    def run(self):
        username = ""
        password = ""
        try:
            username, password = unprotect_account(self._encrypted_account)
            client = ChaoxingClient()
            client.login(username, password)
            data = client.fetch_all_lessons()
            schedule, info = build_schedule(data, self._sync_courses, self._sync_timeline)
            info["synced_at"] = datetime.now().isoformat(timespec="seconds")
            self.succeeded.emit((schedule, info), self._manual)
        except Exception as error:
            self.failed.emit(str(error), self._manual)
        finally:
            username = ""
            password = ""
            self._encrypted_account = ""


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
    syncStateChanged = Signal()
    accountStateChanged = Signal()

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.config = PluginConfig()
        self._provider = None
        self._timer = None
        self._sync_thread = None
        self._account_thread = None
        self._syncing = False
        self._account_checking = False
        self._closing = False
        self._status = ""
        self._current_interval = 0

    def on_load(self):
        super().on_load()
        self.api.config.register_plugin_model(self.pid, self.config)
        icon = self.PATH / "icon.png"
        self._provider = self.api.notification.get_provider(
            self.pid,
            "超星学习通",
            icon=icon if icon.exists() else None,
            use_system_notify=False,
        )
        self.api.ui.register_settings_page(
            "qml/settings.qml",
            title="超星学习通",
            icon="ic_fluent_calendar_20_regular",
        )
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_sync)
        self._apply_config()
        self._refresh_default_status()
        QTimer.singleShot(5000, self._maybe_initial_sync)
        logger.info("Chaoxing schedule plugin loaded")

    def on_unload(self):
        self._closing = True
        if self._timer is not None:
            self._timer.stop()
        for worker in (self._sync_thread, self._account_thread):
            if worker is not None and worker.isRunning():
                worker.wait(1500)
        self._sync_thread = None
        self._account_thread = None
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
        return self.credentialsConfigured and (self.config.sync_courses or self.config.sync_timeline)

    @Property(int, notify=configChanged)
    def intervalMinutes(self) -> int:
        return int(self.config.interval_minutes)

    @Property(str, notify=configChanged)
    def scheduleName(self) -> str:
        return str(self.config.schedule_name)

    @Property(bool, notify=syncStateChanged)
    def syncing(self) -> bool:
        return self._syncing

    @Property(bool, notify=accountStateChanged)
    def accountChecking(self) -> bool:
        return self._account_checking

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:
        return self._status

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
    def saveAccount(self, username: str, password: str) -> bool:
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            self._set_status("请输入手机号/学号和密码")
            return False
        if self._syncing:
            self._set_status("正在同步课表，请稍后再保存账号")
            return False
        if self._account_checking or self._closing:
            return False
        self._account_checking = True
        self.accountStateChanged.emit()
        self._set_status("正在验证账号，请稍候…")
        thread = AccountCheckThread(username, password)
        thread.succeeded.connect(self._on_account_checked)
        thread.failed.connect(self._on_account_failed)
        thread.finished.connect(lambda: ACTIVE_THREADS.discard(thread))
        ACTIVE_THREADS.add(thread)
        self._account_thread = thread
        thread.start()
        return True

    @Slot()
    def clearAccount(self):
        if self._account_checking or self._syncing:
            return
        self.config.encrypted_account = ""
        self.accountStateChanged.emit()
        self._apply_config()
        self._save_config()
        self._set_status("已清除保存的账号")

    @Slot(bool)
    def setSyncCourses(self, enabled: bool):
        self.config.sync_courses = bool(enabled)
        self._apply_config()
        self._set_status(
            "自动同步已开启" if self.config.sync_courses or self.config.sync_timeline
            else "同步课程与时间线均已关闭，自动同步已停止"
        )

    @Slot(bool)
    def setSyncTimeline(self, enabled: bool):
        self.config.sync_timeline = bool(enabled)
        self._apply_config()
        self._set_status(
            "自动同步已开启" if self.config.sync_courses or self.config.sync_timeline
            else "同步课程与时间线均已关闭，自动同步已停止"
        )

    @Slot(int)
    def setIntervalMinutes(self, minutes: int):
        value = max(1, min(int(minutes), 1440))
        self.config.interval_minutes = value
        self._apply_config()
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

    @Slot()
    def triggerSync(self):
        self._start_sync(manual=True)

    def _set_status(self, text: str):
        if text == self._status:
            return
        self._status = text
        self.statusChanged.emit()

    def _save_config(self):
        try:
            self.api.config.save()
        except Exception as error:
            logger.warning(f"Failed to save plugin config: {error}")

    def _refresh_default_status(self):
        if not self.credentialsConfigured:
            self._set_status("请先设置超星学习通账号")
        elif not (self.config.sync_courses or self.config.sync_timeline):
            self._set_status("已配置账号，但同步课程与时间线均已关闭")
        else:
            self._set_status(f"自动同步已开启，间隔 {self.intervalMinutes} 分钟")

    def _apply_config(self):
        if self._timer is not None:
            enabled = self.autoSyncAllowed
            interval = max(1, min(int(self.config.interval_minutes), 1440)) * 60 * 1000
            if interval != self._current_interval:
                self._current_interval = interval
                self._timer.setInterval(interval)
            if enabled:
                if not self._timer.isActive():
                    self._timer.start()
            else:
                self._timer.stop()
        self.configChanged.emit()

    def _maybe_initial_sync(self):
        if self._closing or self._syncing or not self.autoSyncAllowed:
            return
        value = str(self.config.last_sync_at or "")
        due = True
        if value:
            try:
                last = datetime.fromisoformat(value)
                due = datetime.now() - last >= timedelta(minutes=self.intervalMinutes)
            except ValueError:
                due = True
        if due:
            self._start_sync(manual=False)

    def _auto_sync(self):
        if self._closing or not self.autoSyncAllowed:
            return
        self._start_sync(manual=False)

    def _start_sync(self, manual: bool):
        if self._closing or self._syncing or self._account_checking:
            return
        if not self.credentialsConfigured:
            self._set_status("请先设置超星学习通账号")
            if manual:
                self._notify(NotificationLevel.WARNING, "超星学习通", "请先设置账号")
            return
        if not (self.config.sync_courses or self.config.sync_timeline):
            self._set_status("同步课程与时间线均已关闭，自动同步已停止")
            if manual:
                self._notify(NotificationLevel.WARNING, "超星学习通", "请至少开启同步课程或同步时间线")
            return
        self._syncing = True
        self.syncStateChanged.emit()
        self._set_status("正在从超星学习通同步课表…")
        thread = SyncThread(
            self.config.encrypted_account,
            bool(self.config.sync_courses),
            bool(self.config.sync_timeline),
            manual,
        )
        thread.succeeded.connect(self._on_sync_success)
        thread.failed.connect(self._on_sync_failed)
        thread.finished.connect(lambda: ACTIVE_THREADS.discard(thread))
        ACTIVE_THREADS.add(thread)
        self._sync_thread = thread
        thread.start()

    @Slot(str)
    def _on_account_checked(self, encrypted_account: str):
        if self._closing:
            return
        self._account_checking = False
        self.config.encrypted_account = encrypted_account
        self.config.last_sync_at = ""
        self.accountStateChanged.emit()
        self._apply_config()
        self._save_config()
        self._set_status("账号验证成功，已加密保存；重新输入可覆盖旧账号")
        QTimer.singleShot(1000, self._maybe_initial_sync)
        logger.info("Chaoxing account saved")

    @Slot(str)
    def _on_account_failed(self, message: str):
        if self._closing:
            return
        self._account_checking = False
        self.accountStateChanged.emit()
        self._set_status(f"账号验证失败：{message}")

    @Slot(object, bool)
    def _on_sync_success(self, result, manual: bool):
        if self._closing:
            return
        schedule, info = result
        try:
            name, path, is_current = self._write_schedule(schedule)
        except Exception as error:
            self._on_sync_failed(f"保存课表失败：{error}", manual)
            return
        self.config.last_sync_at = info.get("synced_at") or datetime.now().isoformat(timespec="seconds")
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
        if not is_current:
            message += "；可在课表设置中切换到该课表"
        self._set_status(message)
        logger.info(f"Chaoxing schedule synced to {path}")
        if manual:
            self._notify(NotificationLevel.INFO, "超星学习通同步完成", message)

    @Slot(str, bool)
    def _on_sync_failed(self, message: str, manual: bool):
        if self._closing:
            return
        self._syncing = False
        self.syncStateChanged.emit()
        self._set_status(f"同步失败：{message}")
        logger.warning(f"Chaoxing schedule sync failed: {message}")
        if manual:
            self._notify(NotificationLevel.WARNING, "超星学习通同步失败", message)

    def _write_schedule(self, schedule: dict):
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
        if path.exists() and not created:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                marker = str((existing.get("meta") or {}).get("id") or "")
            except Exception:
                marker = ""
            if not marker.startswith("chaoxing-"):
                raise RuntimeError("目标课表已存在且不是本插件创建的，请更换课表名称")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(schedule, file, ensure_ascii=False, indent=4)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        current = str(getattr(self.api.globalconfig.configs.schedule, "current_schedule", ""))
        is_current = current == name
        if is_current:
            self.api.schedule.reload()
        return name, path, is_current

    def _notify(self, level, title: str, message: str):
        if self._provider is None:
            return
        try:
            self._provider.push(level, title, message, 5000, True)
        except Exception as error:
            logger.warning(f"Failed to push notification: {error}")
