import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

FluentPage {
    id: page
    title: "学习通课表同步"
    property string pluginId: "com.msyark.chaoxingsync"
    property var backend: PluginBackendBridge.get_backend(pluginId)
    property bool ready: false
    property var mappingRules: []
    property int editingIndex: -1
    property var editingRule: ({
        "name": "",
        "target": "both",
        "weeks": [],
        "start": "",
        "end": "",
        "title": "",
        "exclude": false
    })
    property string editingTarget: "both"
    property var editingWeeks: []
    property var editingWeekdays: []
    property int editingWeekCount: 20

    readonly property int maxWeek: {
        if (!backend) {
            return 20
        }
        var value = Number(backend.maxWeek)
        if (!isFinite(value) || value < 1) {
            return 20
        }
        return Math.max(1, Math.min(60, Math.floor(value)))
    }

    function normalizeWeeks(value) {
        var result = []
        if (!value || typeof value.length !== "number") {
            return result
        }
        for (var i = 0; i < value.length; i++) {
            var number = parseInt(value[i])
            if (!isNaN(number) && number >= 1 && result.indexOf(number) < 0) {
                result.push(number)
            }
        }
        return result.sort(function(a, b) {
            return a - b
        })
    }

    function normalizeWeekdays(value) {
        var result = []
        if (!value || typeof value.length !== "number") {
            return result
        }
        for (var i = 0; i < value.length; i++) {
            var number = parseInt(value[i])
            if (!isNaN(number) && number >= 1 && number <= 7 && result.indexOf(number) < 0) {
                result.push(number)
            }
        }
        return result.sort(function(a, b) {
            return a - b
        })
    }

    function blankMapping() {
        return {
            name: "",
            target: "both",
            weekdays: [],
            weeks: [],
            start: "",
            end: "",
            title: "",
            exclude: false
        }
    }

    function cloneMapping(rule) {
        var source = rule || {}
        return {
            name: String(source.name || ""),
            target: ["course", "timeline", "both"].indexOf(source.target) >= 0 ? source.target : "both",
            weekdays: normalizeWeekdays(source.weekdays),
            weeks: normalizeWeeks(source.weeks),
            start: String(source.start || ""),
            end: String(source.end || ""),
            title: String(source.title || ""),
            exclude: !!source.exclude
        }
    }

    function parseMappings(text) {
        try {
            var data = JSON.parse(text || "[]")
            if (!data || typeof data.length !== "number") {
                return []
            }
            var result = []
            for (var i = 0; i < data.length; i++) {
                result.push(cloneMapping(data[i]))
            }
            return result
        } catch (error) {
            return []
        }
    }

    function weekdayText(days) {
        var sorted = normalizeWeekdays(days)
        if (sorted.length === 0) {
            return "每天"
        }
        var names = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        var parts = []
        var start = sorted[0]
        var previous = sorted[0]
        for (var i = 1; i <= sorted.length; i++) {
            var current = sorted[i]
            if (current === previous + 1) {
                previous = current
                continue
            }
            parts.push(previous === start
                ? names[start]
                : names[start] + "至" + names[previous])
            start = current
            previous = current
        }
        return parts.join("、")
    }

    function weekRangeText(weeks) {
        var sorted = normalizeWeeks(weeks)
        if (sorted.length === 0) {
            return "全周"
        }
        var parts = []
        var start = sorted[0]
        var previous = sorted[0]
        for (var i = 1; i <= sorted.length; i++) {
            var current = sorted[i]
            if (current === previous + 1) {
                previous = current
                continue
            }
            parts.push(previous === start ? String(start) : start + "-" + previous)
            start = current
            previous = current
        }
        return "第" + parts.join(",") + "周"
    }

    function mappingSummary(rule) {
        var parts = []
        parts.push(rule.target === "course" ? "课程" : rule.target === "timeline" ? "时间线" : "课程 + 时间线")
        if (rule.weekdays && rule.weekdays.length) {
            parts.push(weekdayText(rule.weekdays))
        }
        if (rule.weeks && rule.weeks.length) {
            parts.push(weekRangeText(rule.weeks))
        }
        if (rule.exclude) {
            parts.push("不写入课表")
        } else {
            if (rule.start || rule.end) {
                parts.push((rule.start || "…") + "–" + (rule.end || "…"))
            }
            if (rule.title) {
                parts.push("写入为「" + rule.title + "」")
            }
            if (!rule.start && !rule.end && !rule.title) {
                parts.push("沿用同步数据")
            }
        }
        return parts.join(" · ")
    }

    function weekSelected(week) {
        return editingWeeks.indexOf(parseInt(week)) >= 0
    }

    function toggleWeek(week) {
        var number = parseInt(week)
        if (isNaN(number)) {
            return
        }
        var next = editingWeeks.slice()
        var index = next.indexOf(number)
        if (index >= 0) {
            next.splice(index, 1)
        } else {
            next.push(number)
        }
        editingWeeks = next.sort(function(a, b) {
            return a - b
        })
        updateDialogButtons()
    }

    function selectAllWeeks() {
        var next = []
        for (var week = 1; week <= editingWeekCount; week++) {
            next.push(week)
        }
        editingWeeks = next
        updateDialogButtons()
    }

    function clearWeeks() {
        editingWeeks = []
        updateDialogButtons()
    }

    function weekdaySelected(day) {
        return editingWeekdays.indexOf(parseInt(day)) >= 0
    }

    function toggleWeekday(day) {
        var number = parseInt(day)
        if (isNaN(number) || number < 1 || number > 7) {
            return
        }
        var next = editingWeekdays.slice()
        var index = next.indexOf(number)
        if (index >= 0) {
            next.splice(index, 1)
        } else {
            next.push(number)
        }
        editingWeekdays = next.sort(function(a, b) {
            return a - b
        })
        updateDialogButtons()
    }

    function selectAllWeekdays() {
        editingWeekdays = [1, 2, 3, 4, 5, 6, 7]
        updateDialogButtons()
    }

    function clearWeekdays() {
        editingWeekdays = []
        updateDialogButtons()
    }

    function saveMappings() {
        if (backend) {
            backend.setScheduleMappings(JSON.stringify(mappingRules))
        }
    }

    function removeMapping(index) {
        if (index < 0 || index >= mappingRules.length) {
            return
        }
        var next = mappingRules.slice()
        next.splice(index, 1)
        mappingRules = next
        saveMappings()
    }

    function openMapping(index) {
        if (index >= 0) {
            if (index >= mappingRules.length) {
                return
            }
            editingIndex = index
            editingRule = cloneMapping(mappingRules[index])
        } else {
            editingIndex = -1
            editingRule = blankMapping()
        }
        editingTarget = editingRule.target
        var savedDays = normalizeWeekdays(editingRule.weekdays)
        editingWeekdays = savedDays.length > 0 ? savedDays : [1, 2, 3, 4, 5, 6, 7]
        var selected = []
        var count = page.maxWeek
        var savedWeeks = normalizeWeeks(editingRule.weeks)
        if (savedWeeks.length > 0) {
            selected = savedWeeks.slice()
            for (var i = 0; i < savedWeeks.length; i++) {
                count = Math.max(count, savedWeeks[i])
            }
        } else {
            for (var week = 1; week <= count; week++) {
                selected.push(week)
            }
        }
        editingWeeks = selected
        editingWeekCount = count
        mappingDialog.open()
    }

    function editMapping(index) {
        openMapping(index)
    }

    function addMapping() {
        openMapping(-1)
    }

    function resetMappingEditor() {
        editingIndex = -1
        editingRule = blankMapping()
        editingTarget = "both"
        editingWeeks = []
        editingWeekdays = []
        editingWeekCount = page.maxWeek
    }

    function updateDialogButtons() {
        var okButton = mappingDialog.standardButton(Dialog.Ok)
        if (okButton) {
            okButton.enabled = String(ruleNameField.text || "").trim().length > 0
                && editingWeeks.length > 0
                && editingWeekdays.length > 0
        }
    }

    function commitMapping() {
        var name = String(ruleNameField.text || "").trim()
        if (name.length === 0) {
            return
        }
        var weekdays = normalizeWeekdays(editingWeekdays)
        if (weekdays.length === 0) {
            return
        }
        if (weekdays.length === 7) {
            weekdays = []
        }
        var weeks = normalizeWeeks(editingWeeks)
        if (weeks.length === 0) {
            return
        }
        if (weeks.length === editingWeekCount) {
            weeks = []
        }
        var excluded = ruleExcludeSwitch.checked
        var rule = {
            name: name,
            target: editingTarget,
            weekdays: weekdays,
            weeks: weeks,
            start: excluded ? "" : startField.value,
            end: excluded ? "" : endField.value,
            title: excluded ? "" : String(ruleTitleField.text || "").trim(),
            exclude: excluded
        }
        var next = mappingRules.slice()
        if (editingIndex >= 0 && editingIndex < next.length) {
            next[editingIndex] = rule
        } else {
            next.push(rule)
        }
        mappingRules = next
        saveMappings()
        editingIndex = -1
    }

    function refresh() {
        if (!backend) {
            return
        }
        ready = false
        coursesSwitch.checked = backend.syncCourses
        timelineSwitch.checked = backend.syncTimeline
        intervalSpin.value = backend.intervalMinutes
        breakLimitSpin.value = backend.breakLimitMinutes
        nameField.text = backend.scheduleName
        timetableFileField.text = backend.timetableFile
        timetableUrlField.text = backend.timetableUrl
        timelineServerUrlField.text = backend.timelineServerUrl
        deviceSnField.text = backend.deviceSn
        mappingRules = parseMappings(backend.scheduleMappings)
        notifyStartupSwitch.checked = backend.notifyStartup
        notifyScheduledSwitch.checked = backend.notifyScheduled
        notifyManualSwitch.checked = backend.notifyManual
        ready = true
    }

    Component.onCompleted: {
        if (backend) {
            backend.reloadUiConfig()
        }
        refresh()
    }
    Component.onDestruction: {
        if (phoneField) {
            phoneField.text = ""
        }
        if (passwordField) {
            passwordField.text = ""
        }
    }
    onVisibleChanged: {
        if (visible && backend) {
            backend.reloadUiConfig()
        }
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
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 12

        Frame {
            id: syncBanner
            Layout.fillWidth: true
            hoverable: false
            leftPadding: 20
            rightPadding: 20
            topPadding: 16
            bottomPadding: 16

            readonly property bool syncing: backend ? backend.syncing : false
            readonly property bool failed: backend ? String(backend.statusText).indexOf("同步失败") === 0 : false
            readonly property bool synced: backend ? backend.lastSyncText !== "尚未同步" : false
            readonly property string headline: {
                if (!backend) {
                    return "插件尚未就绪"
                }
                if (backend.syncing) {
                    return "正在同步课表…"
                }
                if (backend.accountChecking) {
                    return "正在验证账号…"
                }
                if (syncBanner.failed) {
                    return "同步失败"
                }
                if (syncBanner.synced) {
                    return "课表已更新"
                }
                return "等待同步"
            }
            readonly property string statusLine: backend ? backend.statusText : "请稍候…"

            ColumnLayout {
                anchors.fill: parent
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14

                    Icon {
                        id: syncBannerIcon
                        Layout.alignment: Qt.AlignVCenter
                        size: 30
                        name: syncBanner.syncing
                            ? "ic_fluent_arrow_sync_20_filled"
                            : backend && backend.accountChecking
                                ? "ic_fluent_person_20_filled"
                                : syncBanner.failed
                                    ? "ic_fluent_error_circle_20_filled"
                                    : syncBanner.synced
                                        ? "ic_fluent_checkmark_circle_20_filled"
                                        : "ic_fluent_info_20_filled"
                        color: syncBanner.syncing
                            ? Theme.currentTheme.colors.primaryColor
                            : syncBanner.failed
                                ? Theme.currentTheme.colors.systemCriticalColor
                                : syncBanner.synced
                                    ? Theme.currentTheme.colors.systemSuccessColor
                                    : Theme.currentTheme.colors.textSecondaryColor

                        RotationAnimation on rotation {
                            running: syncBanner.syncing
                            from: 0
                            to: 360
                            duration: 1200
                            loops: Animation.Infinite
                            onRunningChanged: {
                                if (!running) {
                                    syncBannerIcon.rotation = 0
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 120
                        spacing: 2

                        Text {
                            Layout.fillWidth: true
                            typography: Typography.BodyLarge
                            text: syncBanner.headline
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                            maximumLineCount: 1
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.preferredHeight: statusText.implicitHeight
                            visible: !syncBanner.syncing

                            Text {
                                id: statusText
                                anchors.fill: parent
                                typography: Typography.Caption
                                color: syncBanner.failed
                                    ? Theme.currentTheme.colors.systemCriticalColor
                                    : Theme.currentTheme.colors.textSecondaryColor
                                text: syncBanner.statusLine
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                                maximumLineCount: 1
                                verticalAlignment: Text.AlignVCenter
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton

                                ToolTip {
                                    visible: parent.containsMouse && statusText.truncated
                                    text: syncBanner.statusLine
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.alignment: Qt.AlignVCenter
                        spacing: 6

                        Text {
                            Layout.alignment: Qt.AlignRight
                            Layout.maximumWidth: 200
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                            text: backend ? backend.lastSyncText : "尚未同步"
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                            maximumLineCount: 1
                            horizontalAlignment: Text.AlignRight
                        }

                        Button {
                            Layout.alignment: Qt.AlignRight
                            Layout.minimumWidth: 110
                            text: syncBanner.syncing ? "同步中…" : "立即同步"
                            icon.name: "ic_fluent_arrow_sync_20_regular"
                            highlighted: true
                            enabled: backend
                                && (backend.credentialsConfigured || backend.timetableFile.length > 0 || backend.timetableUrl.length > 0)
                                && !backend.syncing
                                && !backend.accountChecking
                                && (backend.syncCourses || backend.syncTimeline)
                            onClicked: backend.triggerSync()
                        }
                    }
                }

                ProgressBar {
                    Layout.fillWidth: true
                    visible: syncBanner.syncing
                    indeterminate: true
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            icon.name: "ic_fluent_person_20_regular"
            title: "账号"
            description: backend && backend.credentialsConfigured
                ? "已保存加密账号；如需更换，请先清除账号"
                : "尚未设置账号，请先输入手机号/超星号和密码"

            SettingItem {
                Layout.preferredHeight: (!backend || !backend.credentialsConfigured) ? implicitHeight : 0
                clip: true
                enabled: !backend || !backend.credentialsConfigured
                title: "手机号 / 超星号"
                description: "仅用于登录超星学习通"

                TextField {
                    id: phoneField
                    Layout.preferredWidth: 250
                    placeholderText: "请输入手机号或超星号"
                    selectByMouse: true
                    enabled: backend ? !backend.accountChecking : true
                    onTextChanged: {
                        if (backend) {
                            backend.resetAccountVerification()
                        }
                    }
                }
            }

            SettingItem {
                Layout.preferredHeight: (!backend || !backend.credentialsConfigured) ? implicitHeight : 0
                clip: true
                enabled: !backend || !backend.credentialsConfigured
                title: "密码"
                description: "验证并保存后不会显示明文"

                TextField {
                    id: passwordField
                    Layout.preferredWidth: 250
                    placeholderText: "请输入密码"
                    echoMode: TextInput.Password
                    selectByMouse: true
                    enabled: backend ? !backend.accountChecking : true
                    onTextChanged: {
                        if (backend) {
                            backend.resetAccountVerification()
                        }
                    }
                }
            }

            SettingItem {
                title: backend && backend.credentialsConfigured
                    ? "账号已保存"
                    : "验证并保存"
                description: backend && backend.credentialsConfigured
                    ? "账号已加密保存；如需更换，请先清除账号"
                    : backend && backend.accountChecking
                        ? "正在验证账号，请稍候…"
                        : backend && backend.accountVerified
                            ? "验证通过，可以保存账号"
                            : "先验证账号，验证通过后才能保存"

                ColumnLayout {
                    spacing: 6

                    RowLayout {
                        spacing: 8
                        visible: !backend || !backend.credentialsConfigured

                        Button {
                            text: "验证账号"
                            enabled: backend
                                && !backend.accountChecking
                                && !backend.syncing
                                && phoneField.text.length > 0
                                && passwordField.text.length > 0
                            onClicked: backend.verifyAccount(phoneField.text, passwordField.text)
                        }

                        Button {
                            text: "保存账号"
                            highlighted: true
                            enabled: backend
                                && backend.accountVerified
                                && !backend.accountChecking
                                && !backend.syncing
                            onClicked: {
                                if (backend.saveVerifiedAccount()) {
                                    phoneField.text = ""
                                    passwordField.text = ""
                                }
                            }
                        }
                    }

                    Button {
                        text: "清除账号"
                        visible: backend && backend.credentialsConfigured
                        enabled: backend && backend.credentialsConfigured && !backend.accountChecking && !backend.syncing
                        onClicked: backend.clearAccount()
                    }

                    Text {
                        Layout.fillWidth: true
                        typography: Typography.Caption
                        wrapMode: Text.Wrap
                        visible: (!backend || !backend.credentialsConfigured) && text.length > 0
                        text: backend ? backend.accountMessage : ""
                        color: backend && backend.accountError
                            ? Theme.currentTheme.colors.systemCriticalColor
                            : Theme.currentTheme.colors.textSecondaryColor
                    }
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            visible: backend ? backend.showTimetableExport : false
            icon.name: "ic_fluent_document_table_20_regular"
            title: "课表导出文件"
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
            expanded: false
            icon.name: "ic_fluent_clock_20_regular"
            title: "时间线服务器"
            description: "从自建服务器拉取节次时间；课程仍来自课表文件或超星"

            SettingItem {
                title: "服务器地址"
                description: "填写晋元学生平板终端集控系统服务器地址，插件会自动尝试时间线接口"

                TextField {
                    id: timelineServerUrlField
                    Layout.preferredWidth: 250
                    placeholderText: "12.34.56.78:1234"
                    selectByMouse: true
                    onEditingFinished: {
                        if (backend) {
                            backend.setTimelineServerUrl(text)
                        }
                    }
                }
            }

            SettingItem {
                title: "设备 SN"
                description: "用于向晋元学生平板终端集控系统认证设备；由学校提供"

                TextField {
                    id: deviceSnField
                    Layout.preferredWidth: 250
                    placeholderText: "请输入设备 SN"
                    selectByMouse: true
                    onEditingFinished: {
                        if (backend) {
                            backend.setDeviceSn(text)
                        }
                    }
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            icon.name: "ic_fluent_arrow_sync_20_regular"
            title: "自动同步"
            description: backend && backend.autoSyncAllowed && backend.intervalMinutes >= 1
                ? "定时自动同步已开启"
                : backend && backend.autoSyncAllowed
                    ? "定时同步已关闭，仅保留启动同步和手动同步"
                    : "自动同步未开启：需要先保存账号、选择课表文件或填写下载地址，并至少开启同步课程或同步时间线"

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
                description: "开启时获取并写入时间线；关闭时不获取、不写入或替换时间线，仅同步课程"

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
                description: "默认 20 分钟；-1 表示仅启动和手动同步，其余范围 1–1440 分钟"

                SpinBox {
                    id: intervalSpin
                    from: -1
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
                title: "课间上限"
                description: "仅填充不超过该时长的空档；-1 不限制，0 不填充课间"

                SpinBox {
                    id: breakLimitSpin
                    from: -1
                    to: 1440
                    editable: true
                    Layout.preferredWidth: 160
                    onValueChanged: {
                        if (page.ready && backend) {
                            backend.setBreakLimitMinutes(value)
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
            icon.name: "ic_fluent_arrow_shuffle_20_regular"
            title: "日程映射"
            description: "按名称、星期和周次改写课程或时间线的名称、时间，也可以设为不写入课表"

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 10
                Layout.bottomMargin: 14
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Repeater {
                        model: page.mappingRules

                        delegate: ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            Frame {
                                Layout.fillWidth: true
                                hoverable: false
                                radius: 0
                                color: "transparent"
                                border.color: "transparent"
                                leftPadding: 4
                                rightPadding: 4
                                topPadding: 10
                                bottomPadding: 10

                                RowLayout {
                                    anchors.fill: parent
                                    spacing: 10

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2

                                        Text {
                                            Layout.fillWidth: true
                                            text: modelData.name.length > 0 ? modelData.name : "未命名规则"
                                            typography: Typography.Body
                                            elide: Text.ElideRight
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: page.mappingSummary(modelData)
                                            typography: Typography.Caption
                                            color: Theme.currentTheme.colors.textSecondaryColor
                                            elide: Text.ElideRight
                                        }
                                    }

                                    ToolButton {
                                        icon.name: "ic_fluent_edit_20_regular"
                                        onClicked: page.editMapping(index)
                                    }

                                    ToolButton {
                                        icon.name: "ic_fluent_delete_20_regular"
                                        onClicked: page.removeMapping(index)
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.leftMargin: 4
                                Layout.rightMargin: 4
                                height: 1
                                color: Theme.currentTheme.colors.dividerBorderColor
                                visible: index < page.mappingRules.length - 1
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.mappingRules.length === 0
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                    wrapMode: Text.Wrap
                    text: "暂无映射规则；添加后可改写或排除匹配的课程/时间线。"
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Button {
                        text: "添加规则"
                        icon.name: "ic_fluent_add_20_regular"
                        onClicked: page.addMapping()
                    }

                    Text {
                        Layout.fillWidth: true
                        typography: Typography.Caption
                        color: Theme.currentTheme.colors.textSecondaryColor
                        wrapMode: Text.Wrap
                        text: "名称包含匹配，自上而下取第一条；仅选定星期和周次生效，时间或名称留空时沿用同步数据。"
                    }
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


        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            icon.name: "ic_fluent_text_bullet_list_square_20_regular"
            title: "同步详情"
            description: "查看最近一次同步的请求、返回与构建信息"

            SettingItem {
                Layout.fillWidth: true

                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 190
                    clip: true

                    TextArea {
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextArea.Wrap
                        text: backend ? backend.statusDetails : ""
                        color: Theme.currentTheme.colors.textSecondaryColor
                    }
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            expanded: false
            icon.name: "ic_fluent_info_20_regular"
            title: "关于"
            description: "插件仓库、插件广场与作者信息"

            SettingItem {
                title: "GitHub 仓库"
                description: "https://github.com/Michaelshenmo/schedule-from-chaoxing"

                Button {
                    text: "打开"
                    icon.name: "ic_fluent_open_20_regular"
                    onClicked: Qt.openUrlExternally("https://github.com/Michaelshenmo/schedule-from-chaoxing")
                }
            }

            SettingItem {
                title: "插件广场"
                description: "https://plaza.cw.rinlit.cn/plugins/com.msyark.chaoxingsync"

                Button {
                    text: "打开"
                    icon.name: "ic_fluent_open_20_regular"
                    onClicked: Qt.openUrlExternally("https://plaza.cw.rinlit.cn/plugins/com.msyark.chaoxingsync")
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.leftMargin: 58
                Layout.rightMargin: 44
                Layout.topMargin: 10
                Layout.bottomMargin: 12
                Layout.preferredHeight: creditText.implicitHeight

                Text {
                    id: creditText
                    anchors.left: parent.left
                    anchors.right: parent.right
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                    textFormat: Text.RichText
                    wrapMode: Text.Wrap
                    text: '本插件由<a href="https://github.com/Michaelshenmo">Michaelshenmo</a>在DeepSeek V4.1 Flash 1017与GPT 6.1 sol的帮助下完成编写'
                    onLinkActivated: function(link) {
                        Qt.openUrlExternally(link)
                    }
                }
            }
        }
    }

    component MappingTimePicker: RowLayout {
        id: timeField
        property string value: ""
        spacing: 4

        Loader {
            id: pickerLoader
            property string pendingValue: ""
            Layout.preferredWidth: 150
            Layout.preferredHeight: 32
            sourceComponent: Component {
                TimePicker {
                    implicitWidth: 150
                    implicitHeight: 32
                    use24Hour: true
                    hourText: "时"
                    minuteText: "分"
                    contentItem: Text {
                        anchors.fill: parent
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: time !== "" ? time : "选择时间"
                        color: time !== ""
                            ? Theme.currentTheme.colors.textColor
                            : Theme.currentTheme.colors.textSecondaryColor
                    }
                    onTimeChanged: {
                        if (time !== "") {
                            timeField.value = time
                        }
                    }
                }
            }
            onLoaded: {
                if (pendingValue) {
                    item.setTime(pendingValue)
                }
            }
        }

        ToolButton {
            visible: timeField.value !== ""
            icon.name: "ic_fluent_dismiss_20_regular"
            onClicked: timeField.clear()
        }

        function setValue(text) {
            var time = String(text || "")
            value = /^\d{2}:\d{2}$/.test(time) ? time : ""
            pickerLoader.pendingValue = value
            pickerLoader.active = false
            Qt.callLater(function() {
                pickerLoader.active = true
            })
        }

        function clear() {
            setValue("")
        }
    }

    Dialog {
        id: mappingDialog
        title: page.editingIndex >= 0 ? "编辑映射规则" : "添加映射规则"
        modal: true
        width: 480
        standardButtons: Dialog.Ok | Dialog.Cancel
        closePolicy: Popup.NoAutoClose

        onOpened: {
            dialogTabs.currentIndex = 0
            ruleNameField.text = page.editingRule.name
            ruleExcludeSwitch.checked = page.editingRule.exclude
            startField.setValue(page.editingRule.start)
            endField.setValue(page.editingRule.end)
            ruleTitleField.text = page.editingRule.title

            var okButton = mappingDialog.standardButton(Dialog.Ok)
            if (okButton) {
                okButton.text = "保存"
            }
            var cancelButton = mappingDialog.standardButton(Dialog.Cancel)
            if (cancelButton) {
                cancelButton.text = "取消"
            }
            page.updateDialogButtons()
            ruleNameField.forceActiveFocus()
        }

        onAccepted: page.commitMapping()
        onRejected: page.resetMappingEditor()

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 12

            Segmented {
                id: dialogTabs
                Layout.alignment: Qt.AlignHCenter

                SegmentedItem {
                    text: "命中规则"
                    width: 120
                }

                SegmentedItem {
                    text: "操作"
                    width: 100
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: currentItem ? currentItem.implicitHeight : 0
                currentIndex: dialogTabs.currentIndex

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: "名称"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                        }

                        TextField {
                            id: ruleNameField
                            Layout.fillWidth: true
                            placeholderText: "课程或时间线名称，按包含匹配"
                            selectByMouse: true
                            onTextChanged: page.updateDialogButtons()
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: "匹配范围"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 16

                            RadioButton {
                                text: "课程"
                                checked: page.editingTarget === "course"
                                onClicked: page.editingTarget = "course"
                            }

                            RadioButton {
                                text: "时间线"
                                checked: page.editingTarget === "timeline"
                                onClicked: page.editingTarget = "timeline"
                            }

                            RadioButton {
                                text: "同时匹配"
                                checked: page.editingTarget === "both"
                                onClicked: page.editingTarget = "both"
                            }

                            Item {
                                Layout.fillWidth: true
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Text {
                                text: "星期"
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                            }

                            Text {
                                text: page.editingWeekdays.length + "/7"
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            Button {
                                text: "全选"
                                flat: true
                                onClicked: page.selectAllWeekdays()
                            }

                            Button {
                                text: "清空"
                                flat: true
                                onClicked: page.clearWeekdays()
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Repeater {
                                model: 7

                                delegate: Button {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    text: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][index]
                                    checkable: true
                                    checked: page.weekdaySelected(index + 1)
                                    highlighted: checked
                                    onClicked: page.toggleWeekday(index + 1)
                                }
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "只映射所选星期的课程与时间线；未选中的星期保持原样。"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                            wrapMode: Text.Wrap
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: page.editingWeekdays.length === 0
                            text: "至少选择一个星期"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.systemCriticalColor
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Text {
                                text: "周次"
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                            }

                            Text {
                                text: page.editingWeeks.length + "/" + page.editingWeekCount
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            Button {
                                text: "全选"
                                flat: true
                                onClicked: page.selectAllWeeks()
                            }

                            Button {
                                text: "清空"
                                flat: true
                                onClicked: page.clearWeeks()
                            }
                        }

                        Flickable {
                            id: weekFlickable
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(weekFlow.implicitHeight, 110)
                            contentWidth: width
                            contentHeight: weekFlow.implicitHeight
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds

                            ScrollBar.vertical: ScrollBar {}

                            Flow {
                                id: weekFlow
                                width: weekFlickable.width
                                spacing: 6

                                Repeater {
                                    model: page.editingWeekCount

                                    delegate: Button {
                                        width: 40
                                        height: 32
                                        text: String(index + 1)
                                        checkable: true
                                        checked: page.weekSelected(index + 1)
                                        highlighted: checked
                                        onClicked: page.toggleWeek(index + 1)
                                    }
                                }
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "只映射所选周次内的课程与时间线；未选中的周次保持原样。"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                            wrapMode: Text.Wrap
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: page.editingWeeks.length === 0
                            text: "至少选择一个周次"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.systemCriticalColor
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                text: "不写入课表"
                                typography: Typography.Body
                            }

                            Text {
                                Layout.fillWidth: true
                                text: "开启后跳过匹配项，时间与名称设置不再生效"
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                                wrapMode: Text.Wrap
                            }
                        }

                        Switch {
                            id: ruleExcludeSwitch
                            checkedText: ""
                            uncheckedText: ""
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        visible: !ruleExcludeSwitch.checked

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                Layout.preferredWidth: 64
                                text: "开始时间"
                                typography: Typography.Body
                            }

                            MappingTimePicker {
                                id: startField
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                Layout.preferredWidth: 64
                                text: "结束时间"
                                typography: Typography.Body
                            }

                            MappingTimePicker {
                                id: endField
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            Text {
                                text: "写入名称"
                                typography: Typography.Caption
                                color: Theme.currentTheme.colors.textSecondaryColor
                            }

                            TextField {
                                id: ruleTitleField
                                Layout.fillWidth: true
                                placeholderText: "留空沿用同步名称"
                                selectByMouse: true
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "时间未设置时沿用同步数据；名称按包含匹配，规则自上而下取第一条。"
                            typography: Typography.Caption
                            color: Theme.currentTheme.colors.textSecondaryColor
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }
}
