import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1520
    height: 940
    minimumWidth: 1180
    minimumHeight: 760
    title: "Listing Studio · Agent Workspace"
    color: "#0b1020"

    readonly property color panel: "#121a2b"
    readonly property color panelAlt: "#0f1727"
    readonly property color borderColor: "#26344d"
    readonly property color textMain: "#f4f7fb"
    readonly property color textSoft: "#9eacc2"
    readonly property color accent: "#78a9ff"
    readonly property color success: "#69d49f"
    readonly property color warning: "#f4c56b"

    ListModel { id: messagesModel }
    ListModel { id: timelineModel }

    function sendPrompt() {
        var value = composer.text.trim()
        if (value.length === 0 || !agentLab.configured || agentLab.running || agentLab.waitingApproval)
            return
        composer.text = ""
        agentLab.sendMessage(value)
    }

    Connections {
        target: agentLab
        function onMessageAdded(role, text, meta) {
            messagesModel.append({ roleName: role, bodyText: text, metaText: meta })
            Qt.callLater(function() { chatView.positionViewAtEnd() })
        }
        function onTimelineAdded(kind, title, detail) {
            timelineModel.append({ kindName: kind, titleText: title, detailText: detail })
            Qt.callLater(function() { timelineView.positionViewAtEnd() })
        }
        function onSessionReset() {
            messagesModel.clear()
            timelineModel.clear()
        }
    }

    background: Rectangle {
        color: root.color
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#162541" }
            GradientStop { position: 0.50; color: "#0d1627" }
            GradientStop { position: 1.0; color: "#090d18" }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            radius: 16
            color: root.panel
            border.color: root.borderColor

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                spacing: 16

                ColumnLayout {
                    spacing: 2
                    Text {
                        text: "AGENT WORKSPACE · DEVELOPMENT LAB"
                        color: root.accent
                        font.pixelSize: 11
                        font.bold: true
                        font.letterSpacing: 1.1
                    }
                    Text {
                        text: "Commerce Agent Harness"
                        color: root.textMain
                        font.pixelSize: 24
                        font.bold: true
                    }
                }
                Item { Layout.fillWidth: true }
                Rectangle {
                    radius: 10
                    color: agentLab.running ? "#233251" : agentLab.waitingApproval ? "#463821" : "#173128"
                    border.color: agentLab.running ? "#456da8" : agentLab.waitingApproval ? "#8a6d32" : "#2f7055"
                    implicitWidth: statusLabel.implicitWidth + 28
                    implicitHeight: 34
                    Text {
                        id: statusLabel
                        anchors.centerIn: parent
                        text: agentLab.statusText
                        color: agentLab.running ? "#bed5ff" : agentLab.waitingApproval ? root.warning : root.success
                        font.pixelSize: 12
                        font.bold: true
                    }
                }
                Button {
                    text: "新 Session"
                    enabled: agentLab.configured && !agentLab.running
                    onClicked: agentLab.newSession()
                }
            }
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            Rectangle {
                SplitView.preferredWidth: 300
                SplitView.minimumWidth: 270
                color: root.panel
                radius: 16
                border.color: root.borderColor

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 16
                    clip: true

                    ColumnLayout {
                        width: Math.max(230, parent.width - 4)
                        spacing: 10

                        Text { text: "AI CONNECTION"; color: root.accent; font.pixelSize: 11; font.bold: true }
                        Text { text: "Provider / Model"; color: root.textMain; font.pixelSize: 17; font.bold: true }

                        Text { text: "API family"; color: root.textSoft; font.pixelSize: 11 }
                        ComboBox {
                            id: providerCombo
                            Layout.fillWidth: true
                            model: ["OpenAI-compatible", "OpenAI"]
                        }

                        Text { text: "Base URL"; color: root.textSoft; font.pixelSize: 11 }
                        TextField {
                            id: baseUrl
                            Layout.fillWidth: true
                            text: agentLab.dashscopeBaseUrl
                            enabled: providerCombo.currentIndex === 0
                            selectByMouse: true
                        }

                        Text { text: "Model"; color: root.textSoft; font.pixelSize: 11 }
                        TextField {
                            id: modelName
                            Layout.fillWidth: true
                            text: "qwen-plus"
                            placeholderText: "qwen-plus / gpt-..."
                            selectByMouse: true
                        }

                        Text { text: "API Key · memory only"; color: root.textSoft; font.pixelSize: 11 }
                        TextField {
                            id: apiKey
                            Layout.fillWidth: true
                            echoMode: TextInput.Password
                            placeholderText: "sk-..."
                            selectByMouse: true
                        }

                        Button {
                            Layout.fillWidth: true
                            enabled: !agentLab.running
                            text: agentLab.configured ? "重新建立 Agent Runtime" : "建立 Agent Runtime"
                            onClicked: {
                                var adapter = providerCombo.currentIndex === 0 ? "openai-compatible" : "openai"
                                if (agentLab.configureProvider(adapter, baseUrl.text, modelName.text, apiKey.text)) {
                                    apiKey.text = ""
                                    composer.forceActiveFocus()
                                }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderColor }
                        Text { text: "SESSION"; color: root.accent; font.pixelSize: 11; font.bold: true }
                        Text {
                            Layout.fillWidth: true
                            text: agentLab.sessionId.length > 0 ? agentLab.sessionId : "—"
                            color: root.textSoft
                            font.pixelSize: 10
                            wrapMode: Text.WrapAnywhere
                        }
                        Button {
                            Layout.fillWidth: true
                            text: "打开 Session Workspace"
                            enabled: agentLab.workspacePath.length > 0
                            onClicked: agentLab.openWorkspace()
                        }
                        Text {
                            Layout.fillWidth: true
                            text: agentLab.workspacePath.length > 0 ? agentLab.workspacePath : "每个 Session 会获得独立 workspace"
                            color: "#718198"
                            font.pixelSize: 9
                            wrapMode: Text.WrapAnywhere
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderColor }
                        Text { text: "TOKEN USAGE"; color: root.accent; font.pixelSize: 11; font.bold: true }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 3
                            Text { text: agentLab.inputTokens; color: root.textMain; font.pixelSize: 18; font.bold: true }
                            Text { text: agentLab.outputTokens; color: root.textMain; font.pixelSize: 18; font.bold: true }
                            Text { text: agentLab.totalTokens; color: root.textMain; font.pixelSize: 18; font.bold: true }
                            Text { text: "INPUT"; color: root.textSoft; font.pixelSize: 9 }
                            Text { text: "OUTPUT"; color: root.textSoft; font.pixelSize: 9 }
                            Text { text: "TOTAL"; color: root.textSoft; font.pixelSize: 9 }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            visible: agentLab.errorText.length > 0
                            implicitHeight: errorText.implicitHeight + 20
                            radius: 10
                            color: "#351b28"
                            border.color: "#6f3044"
                            Text {
                                id: errorText
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 10
                                text: agentLab.errorText
                                color: "#ffc0cd"
                                wrapMode: Text.WordWrap
                                font.pixelSize: 11
                            }
                        }
                    }
                }
            }

            Rectangle {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 520
                color: root.panelAlt
                radius: 16
                border.color: root.borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            spacing: 1
                            Text { text: "CONVERSATION"; color: root.accent; font.pixelSize: 11; font.bold: true }
                            Text { text: "Agent Session"; color: root.textMain; font.pixelSize: 18; font.bold: true }
                        }
                        Item { Layout.fillWidth: true }
                        Text { text: "Ctrl + Enter 发送"; color: root.textSoft; font.pixelSize: 10 }
                    }

                    ListView {
                        id: chatView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: messagesModel
                        spacing: 10
                        clip: true
                        ScrollBar.vertical: ScrollBar {}

                        delegate: Item {
                            width: chatView.width
                            height: bubble.implicitHeight
                            Rectangle {
                                id: bubble
                                width: Math.min(parent.width * 0.86, Math.max(280, messageBody.implicitWidth + 34))
                                implicitHeight: metaLine.implicitHeight + messageBody.implicitHeight + 30
                                anchors.right: roleName === "user" ? parent.right : undefined
                                anchors.left: roleName === "user" ? undefined : parent.left
                                radius: 14
                                color: roleName === "user" ? "#1d3154" : "#172235"
                                border.color: roleName === "user" ? "#365d91" : "#2a3d59"
                                Text {
                                    id: metaLine
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.leftMargin: 14
                                    anchors.topMargin: 9
                                    text: metaText
                                    color: roleName === "user" ? "#9fc1ff" : "#8fb9a3"
                                    font.pixelSize: 9
                                    font.bold: true
                                }
                                Text {
                                    id: messageBody
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: metaLine.bottom
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    anchors.topMargin: 5
                                    text: bodyText
                                    color: root.textMain
                                    wrapMode: Text.WordWrap
                                    textFormat: Text.PlainText
                                    font.pixelSize: 13
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: agentLab.waitingApproval
                        implicitHeight: approvalColumn.implicitHeight + 24
                        radius: 12
                        color: "#332817"
                        border.color: "#745a2d"
                        ColumnLayout {
                            id: approvalColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 12
                            spacing: 7
                            Text { text: "TOOL APPROVAL"; color: root.warning; font.pixelSize: 10; font.bold: true }
                            Text {
                                Layout.fillWidth: true
                                text: agentLab.approvalTitle
                                color: root.textMain
                                font.pixelSize: 14
                                font.bold: true
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: agentLab.approvalDetail
                                color: root.textSoft
                                font.family: "Consolas"
                                font.pixelSize: 10
                                wrapMode: Text.WrapAnywhere
                            }
                            RowLayout {
                                Item { Layout.fillWidth: true }
                                Button { text: "拒绝"; onClicked: agentLab.resolveApproval(false) }
                                Button { text: "批准并继续"; onClicked: agentLab.resolveApproval(true) }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 7
                        Button {
                            text: "试算 123 × 456"
                            enabled: agentLab.configured && !agentLab.running && !agentLab.waitingApproval
                            onClicked: composer.text = "请务必使用 calculator 工具计算 123 * 456，然后告诉我结果。"
                        }
                        Button {
                            text: "测试 Approval"
                            enabled: agentLab.configured && !agentLab.running && !agentLab.waitingApproval
                            onClicked: composer.text = "请使用 write_workspace_note 工具在工作区写入 test-note.txt，内容是 Agent approval works。"
                        }
                        Button {
                            text: "测试 Workspace"
                            enabled: agentLab.configured && !agentLab.running && !agentLab.waitingApproval
                            onClicked: composer.text = "请使用 list_workspace_files 查看当前 Agent 工作区，并概括里面有什么。"
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.max(78, composer.implicitHeight + 20)
                        radius: 13
                        color: "#101a2c"
                        border.color: composer.activeFocus ? "#4a76b4" : root.borderColor
                        TextArea {
                            id: composer
                            anchors.left: parent.left
                            anchors.right: actionColumn.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.margins: 8
                            enabled: agentLab.configured && !agentLab.running && !agentLab.waitingApproval
                            placeholderText: agentLab.configured ? "给 Agent 一个任务…" : "先在左侧建立 Agent Runtime"
                            wrapMode: TextEdit.Wrap
                            color: root.textMain
                            background: Item {}
                            Keys.onPressed: function(event) {
                                if ((event.modifiers & Qt.ControlModifier) &&
                                    (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                                    root.sendPrompt()
                                    event.accepted = true
                                }
                            }
                        }
                        ColumnLayout {
                            id: actionColumn
                            anchors.right: parent.right
                            anchors.rightMargin: 9
                            anchors.verticalCenter: parent.verticalCenter
                            Button {
                                visible: agentLab.running
                                text: "停止"
                                onClicked: agentLab.cancelCurrent()
                            }
                            Button {
                                visible: !agentLab.running
                                text: "发送"
                                enabled: agentLab.configured && !agentLab.waitingApproval && composer.text.trim().length > 0
                                onClicked: root.sendPrompt()
                            }
                        }
                    }
                }
            }

            Rectangle {
                SplitView.preferredWidth: 330
                SplitView.minimumWidth: 290
                color: root.panel
                radius: 16
                border.color: root.borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 9
                    Text { text: "EXECUTION TRACE"; color: root.accent; font.pixelSize: 11; font.bold: true }
                    Text { text: "Harness Timeline"; color: root.textMain; font.pixelSize: 18; font.bold: true }
                    Text {
                        Layout.fillWidth: true
                        text: "这里展示可观察的 Model / Tool / Approval 事件，不保存模型私有思维链。"
                        color: root.textSoft
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: root.borderColor }

                    ListView {
                        id: timelineView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: timelineModel
                        clip: true
                        spacing: 8
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            width: timelineView.width
                            implicitHeight: eventColumn.implicitHeight + 20
                            radius: 11
                            color: "#111c2e"
                            border.color: kindName === "APPROVAL" ? "#6d582e" : kindName === "TOOL" ? "#315744" : "#293c59"
                            ColumnLayout {
                                id: eventColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 10
                                spacing: 4
                                Text { text: kindName; color: kindName === "APPROVAL" ? root.warning : root.accent; font.pixelSize: 9; font.bold: true }
                                Text {
                                    Layout.fillWidth: true
                                    text: titleText
                                    color: root.textMain
                                    font.pixelSize: 12
                                    font.bold: true
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: detailText.length > 0
                                    text: detailText
                                    color: root.textSoft
                                    font.pixelSize: 10
                                    wrapMode: Text.WrapAnywhere
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: guardText.implicitHeight + 20
                        radius: 10
                        color: "#13251f"
                        border.color: "#2d5948"
                        Text {
                            id: guardText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 9
                            text: "LAB SAFETY\n仅开放 calculator / echo / Workspace 读工具，以及一个需要人工批准的 Workspace 写 note 工具。没有 Makro、Shell、ERP 写权限。"
                            color: "#9ed7bd"
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}
