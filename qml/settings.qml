import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

FluentPage {
    id: page
    title: "超星学习通"
    property string pluginId: "com.michaelshenmo.schedule-from-chaoxing"
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
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: true
            icon.name: "ic_fluent_arrow_sync_20_regular"
            title: "自动同步"
            description: backend && backend.autoSyncAllowed
                ? "自动同步已开启"
                : "自动同步未开启：需要先保存账号，并至少开启同步课程或同步时间线"

            SettingItem {
                title: "同步课程"
                description: "将超星课程名称、教师和地点写入课表"

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
                description: "将上课时间写入课表时间线；关闭后只同步课程信息"

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
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_arrow_sync_checkmark_20_regular"
            title: "立即同步"
            description: "手动拉取一次课表；同步完成后可在课表设置中切换到目标课表"

            Button {
                text: backend && backend.syncing ? "同步中…" : "立即同步"
                enabled: backend
                    && backend.credentialsConfigured
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
