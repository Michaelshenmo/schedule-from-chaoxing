import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

FluentPage {
    id: page
    title: "超星学习通"
    property string pluginId: "com.msyark.chaoxingsync"
    property var backend: PluginBackendBridge.get_backend(pluginId)
    property bool ready: false

    function refresh() {
        if (!backend) {
            return
        }
        ready = false
        coursesSwitch.checked = backend.syncCourses
        timelineSwitch.checked = backend.syncTimeline
        intervalSpin.value = backend.intervalMinutes
        nameField.text = backend.scheduleName
        timetableFileField.text = backend.timetableFile
        timetableUrlField.text = backend.timetableUrl
        notifyStartupSwitch.checked = backend.notifyStartup
        notifyScheduledSwitch.checked = backend.notifyScheduled
        notifyManualSwitch.checked = backend.notifyManual
        ready = true
    }

    Component.onCompleted: refresh()
    Component.onDestruction: {
        if (phoneField) {
            phoneField.text = ""
        }
        if (passwordField) {
            passwordField.text = ""
        }
    }
    onVisibleChanged: {
        if (!visible) {
            if (phoneField) {
                phoneField.text = ""
            }
            if (passwordField) {
                passwordField.text = ""
            }
        }
    }
    onBackendChanged: refresh()

    Connections {
        target: backend
        function onConfigChanged() {
            page.refresh()
        }
        function onAccountStateChanged() {
            page.refresh()
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 12

        SettingExpander {
            Layout.fillWidth: true
            expanded: true
            icon.name: "ic_fluent_person_20_regular"
            title: "账号"
            description: backend && backend.credentialsConfigured
                ? "已保存加密账号，重新输入可覆盖旧账号"
                : "尚未设置账号，请先输入超星学习通手机号/学号和密码"

            SettingItem {
                title: "手机号 / 学号"
                description: "仅用于登录超星学习通"

                TextField {
                    id: phoneField
                    Layout.preferredWidth: 250
                    placeholderText: "请输入手机号或学号"
                    selectByMouse: true
                }
            }

            SettingItem {
                title: "密码"
                description: "保存后不会显示明文"

                TextField {
                    id: passwordField
                    Layout.preferredWidth: 250
                    placeholderText: "请输入密码"
                    echoMode: TextInput.Password
                    selectByMouse: true
                }
            }

            SettingItem {
                title: "保存账号"
                description: backend && backend.accountChecking
                    ? "正在验证账号，请稍候…"
                    : "保存前会先验证登录，成功后加密存储"

                ColumnLayout {
                    spacing: 6

                    RowLayout {
                        spacing: 8

                        Button {
                            text: "保存并验证"
                            highlighted: true
                            enabled: backend
                                && !backend.accountChecking
                                && !backend.syncing
                                && phoneField.text.length > 0
                                && passwordField.text.length > 0
                            onClicked: {
                                if (backend.saveAccount(phoneField.text, passwordField.text)) {
                                    phoneField.text = ""
                                    passwordField.text = ""
                                }
                            }
                        }

                        Button {
                            text: "清除账号"
                            enabled: backend && backend.credentialsConfigured && !backend.accountChecking && !backend.syncing
                            onClicked: backend.clearAccount()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        typography: Typography.Caption
                        wrapMode: Text.Wrap
                        visible: text.length > 0
                        text: backend ? backend.accountMessage : ""
                        color: backend && backend.accountError
                            ? "#C42B1C"
                            : Theme.currentTheme.colors.textSecondaryColor
                    }
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: true
            icon.name: "ic_fluent_document_table_20_regular"
            title: "课表导出文件（可选）"
            description: "可选择本地 .xlsx 课表，或填写学校提供的课表文件下载地址"

            SettingItem {
                title: "文件路径"
                description: "选择后会优先使用该文件，而不是超星课表接口"

                TextField {
                    id: timetableFileField
                    Layout.preferredWidth: 250
                    readOnly: true
                    placeholderText: "未选择文件"
                    selectByMouse: true
                }
            }

            SettingItem {
                title: "下载地址"
                description: "如果学校提供课表文件的直接下载地址，可填在这里；本地文件优先"

                TextField {
                    id: timetableUrlField
                    Layout.preferredWidth: 250
                    placeholderText: "https://…/课表导出.xlsx"
                    selectByMouse: true
                    onEditingFinished: {
                        if (backend) {
                            backend.setTimetableUrl(text)
                        }
                    }
                }
            }

            SettingItem {
                title: "选择文件"
                description: "支持学校系统导出的 .xlsx 课表"

                RowLayout {
                    spacing: 8

                    Button {
                        text: "选择文件"
                        highlighted: true
                        enabled: backend && !backend.syncing
                        onClicked: backend.selectTimetableFile()
                    }

                    Button {
                        text: "清除"
                        enabled: backend && backend.timetableFile.length > 0 && !backend.syncing
                        onClicked: backend.clearTimetableFile()
                    }
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: true
            icon.name: "ic_fluent_arrow_sync_20_regular"
            title: "自动同步"
            description: backend && backend.autoSyncAllowed
                ? "自动同步已开启"
                : "自动同步未开启：需要先保存账号或选择课表文件，并至少开启同步课程或同步时间线"

            SettingItem {
                title: "同步课程"
                description: "从超星课程列表和课表写入课程名称、教师和地点"

                Switch {
                    id: coursesSwitch
                    checked: backend ? backend.syncCourses : true
                    onCheckedChanged: {
                        if (page.ready && backend) {
                            backend.setSyncCourses(checked)
                        }
                    }
                }
            }

            SettingItem {
                title: "同步时间线"
                description: "开启时补全时间线；关闭时仍会按目标课表已有时间线填充课程"

                Switch {
                    id: timelineSwitch
                    checked: backend ? backend.syncTimeline : true
                    onCheckedChanged: {
                        if (page.ready && backend) {
                            backend.setSyncTimeline(checked)
                        }
                    }
                }
            }

            SettingItem {
                title: "同步间隔"
                description: "默认 20 分钟，范围 1–1440 分钟"

                SpinBox {
                    id: intervalSpin
                    from: 1
                    to: 1440
                    editable: true
                    Layout.preferredWidth: 160
                    onValueChanged: {
                        if (page.ready && backend) {
                            backend.setIntervalMinutes(value)
                        }
                    }
                }
            }

            SettingItem {
                title: "目标课表名称"
                description: "同步结果会写入这个独立课表，不会修改你当前的课表"

                TextField {
                    id: nameField
                    Layout.preferredWidth: 250
                    placeholderText: "超星学习通"
                    selectByMouse: true
                    enabled: backend ? !backend.syncing : true
                    onEditingFinished: {
                        if (backend) {
                            backend.setScheduleName(text)
                        }
                    }
                }
            }

            SettingItem {
                title: "查看目标课表"
                description: "切换到插件生成的课表查看同步结果"

                Button {
                    text: "切换课表"
                    enabled: backend && !backend.syncing
                    onClicked: backend.switchToTargetSchedule()
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            icon.name: "ic_fluent_alert_20_regular"
            title: "通知选项"
            description: "分别控制启动时、定时和手动同步的通知"

            SettingItem {
                title: "启动时自动同步"
                description: "Class Widgets 启动后自动同步一次并发送通知"

                Switch {
                    id: notifyStartupSwitch
                    checked: backend ? backend.notifyStartup : true
                    onCheckedChanged: {
                        if (page.ready && backend) {
                            backend.setNotifyStartup(checked)
                        }
                    }
                }
            }

            SettingItem {
                title: "定时自动同步"
                description: "按设定间隔自动同步并发送通知"

                Switch {
                    id: notifyScheduledSwitch
                    checked: backend ? backend.notifyScheduled : false
                    onCheckedChanged: {
                        if (page.ready && backend) {
                            backend.setNotifyScheduled(checked)
                        }
                    }
                }
            }

            SettingItem {
                title: "手动同步"
                description: "点击「立即同步」后发送通知"

                Switch {
                    id: notifyManualSwitch
                    checked: backend ? backend.notifyManual : true
                    onCheckedChanged: {
                        if (page.ready && backend) {
                            backend.setNotifyManual(checked)
                        }
                    }
                }
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_arrow_sync_checkmark_20_regular"
            title: "立即同步"
            description: "手动拉取一次课表；同步完成后可在课表设置中切换到目标课表"

            Button {
                text: backend && backend.syncing ? "同步中…" : "立即同步"
                enabled: backend
                    && (backend.credentialsConfigured || backend.timetableFile.length > 0 || backend.timetableUrl.length > 0)
                    && !backend.syncing
                    && !backend.accountChecking
                    && (backend.syncCourses || backend.syncTimeline)
                highlighted: true
                onClicked: backend.triggerSync()
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_info_20_regular"
            title: "同步状态"
            description: backend ? backend.statusText : "插件尚未就绪"
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_clock_20_regular"
            title: "上次同步"
            description: backend ? backend.lastSyncText : "尚未同步"
        }

        Text {
            Layout.fillWidth: true
            typography: Typography.Caption
            color: Theme.currentTheme.colors.textSecondaryColor
            wrapMode: Text.Wrap
            text: "账号密码会加密保存在 Class Widgets 配置中，设置页面不会回显已保存的账号。"
        }
    }
}
