import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    x: 0
    y: agentWorkspace.contentTop
    width: parent ? parent.width : 0
    height: parent ? Math.max(0, parent.height - y) : 0
    visible: agentWorkspace.open
    enabled: visible
    z: 22000

    readonly property color textMain: Qt.rgba(1, 1, 1, 0.95)
    readonly property color textSoft: Qt.rgba(0.86, 0.91, 0.98, 0.62)
    readonly property color textFaint: Qt.rgba(0.86, 0.91, 0.98, 0.42)
    readonly property color glass: Qt.rgba(0.035, 0.055, 0.09, 0.78)
    readonly property color glassStrong: Qt.rgba(0.028, 0.044, 0.075, 0.91)
    readonly property color glassHover: Qt.rgba(1, 1, 1, 0.075)
    readonly property color border: Qt.rgba(1, 1, 1, 0.10)
    readonly property color borderSoft: Qt.rgba(1, 1, 1, 0.065)
    readonly property color accent: "#9bdcff"
    readonly property color success: "#8fe1b9"
    readonly property color warning: "#f1c77a"
    readonly property color danger: "#f18da0"
    property int inspectorTab: 0

    function sendPrompt() {
        var value = composer.text.trim()
        if (!value.length || !agentWorkspace.configured || agentWorkspace.running || agentWorkspace.waitingApproval)
            return
        composer.text = ""
        agentWorkspace.sendMessage(value)
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.018, 0.028, 0.05, 0.93)
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 18
        anchors.topMargin: 12
        anchors.bottomMargin: 18
        spacing: 10

        Rectangle {
            Layout.preferredWidth: 236
            Layout.minimumWidth: 214
            Layout.fillHeight: true
            radius: 16
            color: root.glass
            border.width: 1
            border.color: root.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        spacing: 1
                        Text { text: "AGENT"; color: root.accent; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.bold: true }
                        Text { text: "任务"; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 19; font.bold: true }
                    }
                    Item { Layout.fillWidth: true }
                    ToolButton {
                        text: "+"
                        enabled: agentWorkspace.configured && !agentWorkspace.running
                        onClicked: agentWorkspace.newSession()
                        font.pixelSize: 18
                        contentItem: Text { text: parent.text; color: root.textMain; font.pixelSize: 18; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 8; color: parent.hovered ? root.glassHover : Qt.rgba(1,1,1,0.035); border.width: 1; border.color: root.borderSoft }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: root.borderSoft }

                ListView {
                    id: sessionsView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: agentWorkspace.sessions
                    clip: true
                    spacing: 6
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: sessionsView.width
                        height: 62
                        radius: 11
                        color: modelData.active ? Qt.rgba(0.24, 0.42, 0.66, 0.24) : hover.hovered ? root.glassHover : "transparent"
                        border.width: modelData.active ? 1 : 0
                        border.color: modelData.active ? Qt.rgba(0.61, 0.86, 1, 0.26) : "transparent"
                        scale: hover.hovered ? 1.01 : 1.0
                        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.BezierSpline; easing.bezierCurve: [0.645,0.045,0.355,1.0,1.0,1.0] } }
                        HoverHandler { id: hover }
                        TapHandler { onTapped: agentWorkspace.selectSession(modelData.sessionId) }
                        Column {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 11; anchors.rightMargin: 11; spacing: 4
                            Text { width: parent.width; text: modelData.title; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight }
                            Row {
                                spacing: 7
                                Text { text: modelData.status === "running" ? "●" : modelData.status === "waiting_approval" ? "◐" : modelData.status === "failed" ? "!" : "✓"; color: modelData.status === "running" ? root.accent : modelData.status === "waiting_approval" ? root.warning : modelData.status === "failed" ? root.danger : root.textFaint; font.pixelSize: 9 }
                                Text { text: modelData.meta; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 9 }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 66
                    radius: 11
                    color: Qt.rgba(1,1,1,0.028)
                    border.width: 1
                    border.color: root.borderSoft
                    Column {
                        anchors.fill: parent; anchors.margins: 10; spacing: 4
                        Text { text: agentWorkspace.configured ? agentWorkspace.modelLabel : "未配置模型"; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 11; font.weight: Font.DemiBold }
                        Text { text: agentWorkspace.configured ? agentWorkspace.providerLabel + " · agent.fast" : "设置 AI 凭据后启用"; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 9 }
                        Text { text: agentWorkspace.statusText; color: agentWorkspace.waitingApproval ? root.warning : agentWorkspace.running ? root.accent : root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 9; elide: Text.ElideRight; width: parent.width }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 520
            radius: 16
            color: root.glassStrong
            border.width: 1
            border.color: root.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        spacing: 1
                        Text { text: "CONVERSATION"; color: root.accent; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.bold: true }
                        Text { text: agentWorkspace.currentSessionId.length ? "Agent Workspace" : "新 Agent 任务"; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 19; font.bold: true }
                    }
                    Item { Layout.fillWidth: true }
                    Text { text: agentWorkspace.running ? "正在执行" : agentWorkspace.waitingApproval ? "等待批准" : "就绪"; color: agentWorkspace.running ? root.accent : agentWorkspace.waitingApproval ? root.warning : root.success; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.bold: true }
                }

                ListView {
                    id: conversationView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: agentWorkspace.conversationItems
                    clip: true
                    spacing: 9
                    ScrollBar.vertical: ScrollBar {}
                    onCountChanged: Qt.callLater(function() { positionViewAtEnd() })
                    delegate: Item {
                        required property var modelData
                        width: conversationView.width
                        height: contentLoader.item ? contentLoader.item.implicitHeight : 0
                        Loader {
                            id: contentLoader
                            width: parent.width
                            sourceComponent: modelData.kind === "tool" ? toolCard : messageBubble
                            property var row: modelData
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: agentWorkspace.waitingApproval
                    implicitHeight: approvalColumn.implicitHeight + 22
                    radius: 13
                    color: Qt.rgba(0.29, 0.22, 0.10, 0.58)
                    border.width: 1
                    border.color: Qt.rgba(0.95, 0.78, 0.42, 0.34)
                    ColumnLayout {
                        id: approvalColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11; spacing: 7
                        Text { text: "需要你的批准"; color: root.warning; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.bold: true }
                        Text { Layout.fillWidth: true; text: agentWorkspace.approvalTitle; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true; wrapMode: Text.WordWrap }
                        Text { Layout.fillWidth: true; text: agentWorkspace.approvalDetail; color: root.textSoft; font.family: "Consolas"; font.pixelSize: 9; wrapMode: Text.WrapAnywhere; maximumLineCount: 6; elide: Text.ElideRight }
                        RowLayout {
                            Item { Layout.fillWidth: true }
                            Button { text: "拒绝"; onClicked: agentWorkspace.resolveApproval(false) }
                            Button { text: "批准一次"; onClicked: agentWorkspace.resolveApproval(true) }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.max(74, composer.implicitHeight + 18)
                    radius: 13
                    color: Qt.rgba(0,0,0,0.24)
                    border.width: 1
                    border.color: composer.activeFocus ? Qt.rgba(0.61,0.86,1,0.34) : root.borderSoft
                    TextArea {
                        id: composer
                        anchors.left: parent.left; anchors.right: actionStrip.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        anchors.margins: 8
                        enabled: agentWorkspace.configured && !agentWorkspace.running && !agentWorkspace.waitingApproval
                        placeholderText: agentWorkspace.configured ? "给 Agent 一个任务…" : "请先配置 Agent AI 凭据"
                        color: root.textMain
                        placeholderTextColor: root.textFaint
                        wrapMode: TextEdit.Wrap
                        background: Item {}
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 12
                        Keys.onPressed: function(event) {
                            if ((event.modifiers & Qt.ControlModifier) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                                root.sendPrompt(); event.accepted = true
                            }
                        }
                    }
                    Row {
                        id: actionStrip
                        anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter; spacing: 7
                        Rectangle {
                            implicitWidth: modelText.implicitWidth + 18; implicitHeight: 30; radius: 8
                            color: Qt.rgba(1,1,1,0.045); border.width: 1; border.color: root.borderSoft
                            Text { id: modelText; anchors.centerIn: parent; text: agentWorkspace.modelLabel; color: root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 9 }
                        }
                        Button {
                            text: agentWorkspace.running ? "■" : "↑"
                            enabled: agentWorkspace.running || (agentWorkspace.configured && !agentWorkspace.waitingApproval && composer.text.trim().length > 0)
                            onClicked: agentWorkspace.running ? agentWorkspace.cancelCurrent() : root.sendPrompt()
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: agentWorkspace.errorText.length > 0
                    text: agentWorkspace.errorText
                    color: root.danger
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 9
                    wrapMode: Text.WordWrap
                }
            }
        }

        Rectangle {
            Layout.preferredWidth: 312
            Layout.minimumWidth: 286
            Layout.fillHeight: true
            radius: 16
            color: root.glass
            border.width: 1
            border.color: root.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 9

                Text { text: "WORKSPACE"; color: root.accent; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.bold: true }
                RowLayout {
                    Layout.fillWidth: true
                    Repeater {
                        model: ["活动", "文件", "详情"]
                        delegate: Rectangle {
                            required property string modelData
                            required property int index
                            Layout.fillWidth: true
                            height: 29
                            radius: 8
                            color: root.inspectorTab === index ? Qt.rgba(1,1,1,0.075) : hoverTab.hovered ? Qt.rgba(1,1,1,0.04) : "transparent"
                            HoverHandler { id: hoverTab }
                            TapHandler { onTapped: root.inspectorTab = index }
                            Text { anchors.centerIn: parent; text: modelData; color: root.inspectorTab === index ? root.textMain : root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; font.weight: root.inspectorTab === index ? Font.DemiBold : Font.Normal }
                        }
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: root.borderSoft }

                Loader {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sourceComponent: root.inspectorTab === 0 ? activityPanel : root.inspectorTab === 1 ? filesPanel : detailsPanel
                }
            }
        }
    }

    Component {
        id: messageBubble
        Item {
            implicitHeight: bubble.implicitHeight
            Rectangle {
                id: bubble
                width: Math.min(parent.width * 0.84, Math.max(270, body.implicitWidth + 34))
                implicitHeight: body.implicitHeight + 34
                anchors.right: row.role === "user" ? parent.right : undefined
                anchors.left: row.role === "user" ? undefined : parent.left
                radius: 13
                color: row.role === "user" ? Qt.rgba(0.13,0.25,0.41,0.72) : Qt.rgba(1,1,1,0.045)
                border.width: 1
                border.color: row.role === "user" ? Qt.rgba(0.40,0.65,0.95,0.26) : root.borderSoft
                Text { anchors.left: parent.left; anchors.top: parent.top; anchors.leftMargin: 13; anchors.topMargin: 8; text: row.title + "  " + row.meta; color: row.role === "user" ? root.accent : root.success; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; font.bold: true }
                Text { id: body; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 13; anchors.topMargin: 22; text: row.body; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 12; wrapMode: Text.WordWrap; textFormat: Text.PlainText }
            }
        }
    }

    Component {
        id: toolCard
        Item {
            implicitHeight: card.implicitHeight
            Rectangle {
                id: card
                width: Math.min(parent.width * 0.78, 520)
                implicitHeight: toolColumn.implicitHeight + 20
                anchors.left: parent.left
                radius: 12
                color: Qt.rgba(1,1,1,0.035)
                border.width: 1
                border.color: row.state === "failed" || row.state === "denied" ? Qt.rgba(0.95,0.55,0.63,0.30) : row.state === "approval" ? Qt.rgba(0.95,0.78,0.42,0.30) : Qt.rgba(0.56,0.88,0.73,0.18)
                Column {
                    id: toolColumn
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 4
                    Row {
                        spacing: 7
                        Text { text: row.state === "done" ? "✓" : row.state === "failed" ? "!" : row.state === "approval" ? "◐" : "◇"; color: row.state === "failed" ? root.danger : row.state === "approval" ? root.warning : row.state === "done" ? root.success : root.accent; font.pixelSize: 11 }
                        Text { text: row.title; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                    Text { width: parent.width; text: row.body; color: root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 9; wrapMode: Text.WordWrap; maximumLineCount: 4; elide: Text.ElideRight }
                }
            }
        }
    }

    Component {
        id: activityPanel
        ListView {
            model: agentWorkspace.activityItems
            clip: true
            spacing: 7
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                implicitHeight: activityColumn.implicitHeight + 18
                radius: 10
                color: Qt.rgba(1,1,1,0.028)
                border.width: 1
                border.color: root.borderSoft
                Column {
                    id: activityColumn
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 9; spacing: 3
                    Text { width: parent.width; text: modelData.time + "   " + modelData.title; color: modelData.tone === "error" ? root.danger : modelData.tone === "approval" ? root.warning : modelData.tone === "success" ? root.success : root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 9; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { width: parent.width; text: modelData.detail; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight }
                }
            }
        }
    }

    Component {
        id: filesPanel
        ColumnLayout {
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Text { text: agentWorkspace.workspaceFiles.length + " files"; color: root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 9 }
                Item { Layout.fillWidth: true }
                Button { text: "刷新"; onClicked: agentWorkspace.refreshWorkspaceFiles() }
            }
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true
                model: agentWorkspace.workspaceFiles
                clip: true; spacing: 5
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 48; radius: 9
                    color: fileHover.hovered ? root.glassHover : Qt.rgba(1,1,1,0.025)
                    HoverHandler { id: fileHover }
                    TapHandler { onDoubleTapped: agentWorkspace.openWorkspaceFile(modelData.path) }
                    Column { anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.leftMargin: 9; anchors.rightMargin: 9; spacing: 3
                        Text { width: parent.width; text: modelData.name; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; elide: Text.ElideMiddle }
                        Text { width: parent.width; text: modelData.path + " · " + modelData.meta; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; elide: Text.ElideMiddle }
                    }
                }
            }
            Button { Layout.fillWidth: true; text: "在资源管理器中打开"; enabled: agentWorkspace.workspacePath.length > 0; onClicked: agentWorkspace.openWorkspaceFolder() }
        }
    }

    Component {
        id: detailsPanel
        ColumnLayout {
            spacing: 11
            Text { text: "MODEL"; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; font.bold: true }
            Text { text: agentWorkspace.modelLabel; color: root.textMain; font.family: "Microsoft YaHei UI"; font.pixelSize: 13; font.bold: true }
            Text { text: agentWorkspace.providerLabel + " · agent.fast"; color: root.textSoft; font.family: "Microsoft YaHei UI"; font.pixelSize: 9 }
            Rectangle { Layout.fillWidth: true; height: 1; color: root.borderSoft }
            Text { text: "TOKEN USAGE"; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; font.bold: true }
            GridLayout {
                Layout.fillWidth: true; columns: 3
                Text { text: agentWorkspace.inputTokens; color: root.textMain; font.pixelSize: 17; font.bold: true }
                Text { text: agentWorkspace.outputTokens; color: root.textMain; font.pixelSize: 17; font.bold: true }
                Text { text: agentWorkspace.totalTokens; color: root.textMain; font.pixelSize: 17; font.bold: true }
                Text { text: "INPUT"; color: root.textFaint; font.pixelSize: 8 }
                Text { text: "OUTPUT"; color: root.textFaint; font.pixelSize: 8 }
                Text { text: "TOTAL"; color: root.textFaint; font.pixelSize: 8 }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: root.borderSoft }
            Text { text: "SESSION"; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 8; font.bold: true }
            Text { Layout.fillWidth: true; text: agentWorkspace.currentSessionId || "—"; color: root.textSoft; font.family: "Consolas"; font.pixelSize: 8; wrapMode: Text.WrapAnywhere }
            Text { Layout.fillWidth: true; text: "Agent 只保存可观察的消息、工具调用、批准与执行事件；不保存模型私有思维链。"; color: root.textFaint; font.family: "Microsoft YaHei UI"; font.pixelSize: 9; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
        }
    }
}
