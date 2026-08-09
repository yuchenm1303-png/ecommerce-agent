import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: app
    width: 1600
    height: 1080
    minimumWidth: 1240
    minimumHeight: 860
    visible: true
    title: "ecommerce-agent · Acceptance Control Console"
    color: "#17263a"

    property int layoutEpoch: 0
    onWidthChanged: layoutEpoch++
    onHeightChanged: layoutEpoch++

    component SmallText: Text {
        color: "#a5ffffff"
        font.pixelSize: 11
        elide: Text.ElideRight
    }

    component Eyebrow: Text {
        color: "#8cffffff"
        font.pixelSize: 10
        font.weight: Font.DemiBold
        font.letterSpacing: 1
    }

    component CardTitle: Text {
        color: "white"
        font.pixelSize: 14
        font.weight: Font.Bold
    }

    component DarkField: TextField {
        color: "white"
        placeholderTextColor: "#6fffffff"
        selectionColor: "#40ffffff"
        selectedTextColor: "white"
        font.pixelSize: 12
        leftPadding: 11
        rightPadding: 11
        background: Rectangle {
            radius: 6
            color: parent.activeFocus ? "#56000000" : (parent.hovered ? "#4e000000" : "#40000000")
            border.width: 1
            border.color: parent.activeFocus ? "#60ffffff" : "#1cffffff"
        }
    }

    component DarkButton: Button {
        id: control
        implicitHeight: 38
        leftPadding: 14
        rightPadding: 14
        contentItem: Text {
            text: control.text
            color: control.enabled ? "white" : "#4cffffff"
            font.pixelSize: 12
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: !control.enabled ? "#22000000" : control.down ? "#40000000" : control.hovered ? "#66000000" : "#40000000"
        }
    }

    component PrimaryButton: DarkButton {
        background: Rectangle {
            radius: 6
            color: !parent.enabled ? "#22000000" : parent.down ? "#30000000" : parent.hovered ? "#48ffffff" : "#30ffffff"
        }
    }

    component DarkCheck: CheckBox {
        id: box
        contentItem: Text {
            text: box.text
            color: box.enabled ? "#d2ffffff" : "#55ffffff"
            font.pixelSize: 11
            verticalAlignment: Text.AlignVCenter
            leftPadding: box.indicator.width + box.spacing
        }
        indicator: Rectangle {
            implicitWidth: 16
            implicitHeight: 16
            x: 0
            y: (box.height - height) / 2
            radius: 4
            color: box.checked ? "#76ffffff" : "#40000000"
            border.width: 1
            border.color: box.checked ? "#beffffff" : "#48ffffff"
        }
    }

    component DarkCombo: ComboBox {
        id: combo
        implicitHeight: 38
        leftPadding: 10
        rightPadding: 28
        contentItem: Text {
            text: combo.displayText
            color: "white"
            font.pixelSize: 11
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 6
            color: combo.hovered ? "#4e000000" : "#40000000"
            border.width: 1
            border.color: "#1cffffff"
        }
        popup: Popup {
            y: combo.height + 2
            width: combo.width
            implicitHeight: Math.min(contentItem.implicitHeight, 360)
            padding: 4
            background: Rectangle { color: "#e51b2532"; radius: 6; border.color: "#30ffffff" }
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: combo.popup.visible ? combo.delegateModel : null
                currentIndex: combo.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator { }
            }
        }
        delegate: ItemDelegate {
            width: combo.width - 8
            contentItem: Text {
                text: modelData.label !== undefined ? modelData.label : modelData
                color: "white"
                font.pixelSize: 11
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle { radius: 4; color: highlighted ? "#32ffffff" : "transparent" }
        }
    }

    SceneBackground {
        id: scene
        anchors.fill: parent
    }

    ColumnLayout {
        id: shell
        anchors.fill: parent
        anchors.leftMargin: 30
        anchors.rightMargin: 30
        anchors.topMargin: 24
        anchors.bottomMargin: 24
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 65
            spacing: 14

            ColumnLayout {
                spacing: 1
                Eyebrow { text: "LOCAL DEVELOPMENT · ACCEPTANCE CONTROL" }
                Text { text: "ecommerce-agent"; color: "white"; font.pixelSize: 31; font.weight: Font.Bold }
                SmallText { text: "供应商 URL → fresh schema → cold/hot Resolver → Fill Plan → gated real execution"; Layout.preferredWidth: 760 }
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                radius: 6
                color: "#40000000"
                implicitHeight: 38
                implicitWidth: phaseLabel.implicitWidth + 26
                Text {
                    id: phaseLabel
                    anchors.centerIn: parent
                    text: bridge.phaseText
                    color: "#efefef"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
            }
            DarkButton {
                text: "打开结果目录"
                enabled: !bridge.readRunning
                onClicked: bridge.openRunDir()
            }
        }

        GlassCard {
            id: inputCard
            Layout.fillWidth: true
            Layout.preferredHeight: 224
            blurScene: scene.blurScene
            sceneX: { app.layoutEpoch; return inputCard.mapToItem(app.contentItem, 0, 0).x }
            sceneY: { app.layoutEpoch; return inputCard.mapToItem(app.contentItem, 0, 0).y }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 15
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout { spacing: 0; Eyebrow { text: "PRODUCT SOURCE" }; CardTitle { text: "商品来源" } }
                    SmallText { text: "只输入一个 1688 / supplier 商品 URL；AI 与浏览器链保持现有实现。"; Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 9
                    DarkField {
                        id: urlInput
                        Layout.fillWidth: true
                        implicitHeight: 38
                        placeholderText: "https://detail.1688.com/offer/..."
                        enabled: !bridge.readRunning && !bridge.realRunning
                        onAccepted: bridge.startReadOnly(text, makroPort.value, sourcePort.value, verticalInput.text, currentPage.checked)
                    }
                    PrimaryButton {
                        text: "只读测试"
                        enabled: !bridge.readRunning && !bridge.realRunning
                        onClicked: bridge.startReadOnly(urlInput.text, makroPort.value, sourcePort.value, verticalInput.text, currentPage.checked)
                    }
                    DarkButton { text: "停止"; enabled: bridge.readRunning; onClicked: bridge.stopReadOnly() }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text { text: "Makro CDP"; color: "#b8ffffff"; font.pixelSize: 10 }
                    SpinBox { id: makroPort; from: 1; to: 65535; value: 9222; enabled: !bridge.readRunning && !bridge.realRunning; implicitWidth: 105 }
                    Text { text: "Source CDP"; color: "#b8ffffff"; font.pixelSize: 10 }
                    SpinBox { id: sourcePort; from: 1; to: 65535; value: 9333; enabled: !bridge.readRunning && !bridge.realRunning; implicitWidth: 105 }
                    DarkField { id: verticalInput; text: "vehicle_camera_system"; Layout.preferredWidth: 220; implicitHeight: 34; enabled: !bridge.readRunning && !bridge.realRunning }
                    DarkCheck { id: currentPage; text: "Source Edge 已人工验证：采集当前页"; enabled: !bridge.readRunning && !bridge.realRunning }
                    Item { Layout.fillWidth: true }
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#18ffffff" }

                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout { spacing: 0; Eyebrow { text: "REAL BROWSER ACCEPTANCE · EXPLICIT PERMISSIONS" }; CardTitle { text: "真实网页填写验收" } }
                    SmallText { text: bridge.realUnlocked ? "read-only 已通过；真实填写已解锁，Save / 图片仍需显式授权，QC 永久锁定。" : "完成 read-only 四阶段后才解锁真实填写。"; Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    DarkCombo {
                        id: scopeCombo
                        Layout.preferredWidth: 300
                        model: bridge.realScopes
                        textRole: "label"
                        currentIndex: Math.min(1, count - 1)
                        enabled: !bridge.realRunning
                    }
                    DarkCheck { id: saveCheck; text: "允许 Save + reopen"; enabled: !bridge.realRunning }
                    DarkCheck {
                        id: uploadCheck
                        text: "上传图片"
                        enabled: !bridge.realRunning && (scopeCombo.currentIndex === scopeCombo.count - 1 || scopeCombo.currentText.indexOf("Product Photos") >= 0)
                    }
                    DarkButton { text: "选择图片…"; enabled: uploadCheck.checked && !bridge.realRunning; onClicked: fileDialog.open() }
                    SmallText { text: bridge.selectedImageCount + " files" }
                    DarkCheck { text: "Send to QC · LOCKED"; checked: false; enabled: false }
                    Item { Layout.fillWidth: true }
                    PrimaryButton {
                        text: "真实填写测试"
                        enabled: bridge.realUnlocked && !bridge.readRunning && !bridge.realRunning
                        onClicked: realConfirm.open()
                    }
                    DarkButton { text: "停止真实测试"; enabled: bridge.realRunning; onClicked: bridge.stopReal() }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 92
            spacing: 11

            StatusCard { id: readyCard; Layout.fillWidth: true; Layout.fillHeight: true; blurScene: scene.blurScene; sceneX: { app.layoutEpoch; return readyCard.mapToItem(app.contentItem,0,0).x }; sceneY: { app.layoutEpoch; return readyCard.mapToItem(app.contentItem,0,0).y }; titleText: "READY"; captionText: "Final Fill Plan"; valueText: bridge.counts.ready < 0 ? "—" : bridge.counts.ready; valueColor: "#8fe1b9" }
            StatusCard { id: missingCard; Layout.fillWidth: true; Layout.fillHeight: true; blurScene: scene.blurScene; sceneX: { app.layoutEpoch; return missingCard.mapToItem(app.contentItem,0,0).x }; sceneY: { app.layoutEpoch; return missingCard.mapToItem(app.contentItem,0,0).y }; titleText: "MISSING"; captionText: "AI final packet"; valueText: bridge.counts.missing < 0 ? "—" : bridge.counts.missing; valueColor: "#f4cb7a" }
            StatusCard { id: conflictCard; Layout.fillWidth: true; Layout.fillHeight: true; blurScene: scene.blurScene; sceneX: { app.layoutEpoch; return conflictCard.mapToItem(app.contentItem,0,0).x }; sceneY: { app.layoutEpoch; return conflictCard.mapToItem(app.contentItem,0,0).y }; titleText: "CONFLICT"; captionText: "AI final packet"; valueText: bridge.counts.conflict < 0 ? "—" : bridge.counts.conflict; valueColor: "#f18da0" }
            StatusCard { id: blockedCard; Layout.fillWidth: true; Layout.fillHeight: true; blurScene: scene.blurScene; sceneX: { app.layoutEpoch; return blockedCard.mapToItem(app.contentItem,0,0).x }; sceneY: { app.layoutEpoch; return blockedCard.mapToItem(app.contentItem,0,0).y }; titleText: "BLOCKED"; captionText: "Final hard/business gate"; valueText: bridge.counts.blocked < 0 ? "—" : bridge.counts.blocked; valueColor: "#e796ae" }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 220
            spacing: 11

            GlassCard {
                id: fieldsCard
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 720
                blurScene: scene.blurScene
                sceneX: { app.layoutEpoch; return fieldsCard.mapToItem(app.contentItem,0,0).x }
                sceneY: { app.layoutEpoch; return fieldsCard.mapToItem(app.contentItem,0,0).y }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 13
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout { spacing: 0; Eyebrow { text: "FIELD RESOLUTION · FULL TRACE" }; CardTitle { text: "字段决策与最终 Gate" } }
                        Item { Layout.fillWidth: true }
                        SmallText { text: bridge.fieldsHint }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 30
                        color: "#18ffffff"
                        Row {
                            anchors.fill: parent
                            property real unit: width / 100
                            Text { width: parent.unit*15; text: "字段名"; color: "#dcffffff"; font.pixelSize: 10; leftPadding: 6; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*9; text: "AI 状态"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*20; text: "AI 结果"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*10; text: "最终状态"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*18; text: "blocked / gate 原因"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*18; text: "来源"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                            Text { width: parent.unit*10; text: "Field ID"; color: "#dcffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height }
                        }
                    }
                    ListView {
                        id: fieldList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: bridge.fields
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: Rectangle {
                            required property var modelData
                            width: fieldList.width
                            height: 38
                            color: index % 2 ? "#08ffffff" : "#10000000"
                            Row {
                                anchors.fill: parent
                                property real unit: width / 100
                                function cell(t, w, c) { return null }
                                Text { width: parent.unit*15; text: modelData.fieldName; color: "white"; font.pixelSize: 10; leftPadding: 6; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*9; text: modelData.aiStatus; color: "#d8ffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*20; text: modelData.aiResult; color: "white"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*10; text: modelData.finalStatus; color: modelData.finalStatus === "READY" ? "#8fe1b9" : modelData.finalStatus === "BLOCKED" ? "#e796ae" : "#f4cb7a"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*18; text: modelData.blockedReason; color: "#c8ffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*18; text: modelData.source; color: "#c8ffffff"; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                                Text { width: parent.unit*10; text: modelData.fieldId; color: "#9effffff"; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter; height: parent.height; elide: Text.ElideRight }
                            }
                        }
                    }
                }
            }

            ScrollView {
                id: sideScroll
                Layout.preferredWidth: 410
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                onContentItemChanged: app.layoutEpoch++

                Column {
                    id: sideColumn
                    width: sideScroll.availableWidth
                    spacing: 11

                    GlassCard {
                        id: telemetryCard
                        width: sideColumn.width
                        height: 222
                        blurScene: scene.blurScene
                        sceneX: { app.layoutEpoch; sideScroll.contentItem.contentY; return telemetryCard.mapToItem(app.contentItem,0,0).x }
                        sceneY: { app.layoutEpoch; sideScroll.contentItem.contentY; return telemetryCard.mapToItem(app.contentItem,0,0).y }
                        Column {
                            anchors.fill: parent; anchors.margins: 13; spacing: 5
                            Eyebrow { text: "RUN DIAGNOSTICS · MODEL / CACHE" }
                            CardTitle { text: "Resolver Telemetry" }
                            Repeater { model: bridge.telemetry; SmallText { width: telemetryCard.width - 26; text: modelData } }
                        }
                    }

                    GlassCard {
                        id: webCard
                        width: sideColumn.width
                        height: 230
                        blurScene: scene.blurScene
                        sceneX: { app.layoutEpoch; sideScroll.contentItem.contentY; return webCard.mapToItem(app.contentItem,0,0).x }
                        sceneY: { app.layoutEpoch; sideScroll.contentItem.contentY; return webCard.mapToItem(app.contentItem,0,0).y }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 13; spacing: 6
                            RowLayout { Layout.fillWidth: true; ColumnLayout { spacing: 0; Eyebrow { text: "ENTITY MATCH" }; CardTitle { text: "Web candidates" } }; Item { Layout.fillWidth: true }; SmallText { text: bridge.webHint } }
                            ListView {
                                id: webList
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.webCandidates
                                ScrollBar.vertical: ScrollBar { }
                                delegate: Rectangle {
                                    required property var modelData
                                    width: webList.width; height: 52; color: index % 2 ? "#08ffffff" : "#10000000"
                                    Column { anchors.fill: parent; anchors.margins: 5; spacing: 2
                                        Row { spacing: 7
                                            Text { text: modelData.match; color: modelData.match === "SAME_PRODUCT" ? "#8fe1b9" : modelData.match === "DIFFERENT_PRODUCT" ? "#f18da0" : "#f4cb7a"; font.pixelSize: 10; font.weight: Font.DemiBold }
                                            Text { width: webList.width - 115; text: modelData.source; color: "white"; font.pixelSize: 10; elide: Text.ElideRight }
                                        }
                                        Text { width: webList.width - 10; text: modelData.reason; color: "#a8ffffff"; font.pixelSize: 9; elide: Text.ElideRight }
                                    }
                                }
                            }
                        }
                    }

                    GlassCard {
                        id: safetyCard
                        width: sideColumn.width
                        height: 132
                        blurScene: scene.blurScene
                        sceneX: { app.layoutEpoch; sideScroll.contentItem.contentY; return safetyCard.mapToItem(app.contentItem,0,0).x }
                        sceneY: { app.layoutEpoch; sideScroll.contentItem.contentY; return safetyCard.mapToItem(app.contentItem,0,0).y }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 13; spacing: 5
                            Eyebrow { text: "ZERO-WRITE / REAL EXECUTION SAFETY" }
                            CardTitle { text: "Makro write safety" }
                            RowLayout { Layout.fillWidth: true
                                SmallText { text: "Makro Write"; Layout.fillWidth: true }
                                Text { text: bridge.safety.writes; color: bridge.safety.safe ? "#8fe1b9" : "#f18da0"; font.pixelSize: 11; font.weight: Font.Bold }
                            }
                            RowLayout { Layout.fillWidth: true
                                SmallText { text: "Save"; Layout.fillWidth: true }
                                Text { text: bridge.safety.save; color: bridge.safety.safe ? "#8fe1b9" : "#f18da0"; font.pixelSize: 11; font.weight: Font.Bold }
                            }
                            RowLayout { Layout.fillWidth: true
                                SmallText { text: "Send to QC"; Layout.fillWidth: true }
                                Text { text: bridge.safety.qc; color: bridge.safety.qc.indexOf("YES") === 0 ? "#f18da0" : "#8fe1b9"; font.pixelSize: 11; font.weight: Font.Bold }
                            }
                        }
                    }
                }
            }
        }

        GlassCard {
            id: consoleCard
            Layout.fillWidth: true
            Layout.preferredHeight: 258
            Layout.minimumHeight: 190
            blurScene: scene.blurScene
            sceneX: { app.layoutEpoch; return consoleCard.mapToItem(app.contentItem,0,0).x }
            sceneY: { app.layoutEpoch; return consoleCard.mapToItem(app.contentItem,0,0).y }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 5

                RowLayout {
                    Layout.fillWidth: true
                    TabBar {
                        id: consoleTabs
                        Layout.fillWidth: true
                        background: Rectangle { color: "transparent" }
                        TabButton { text: "Read-only Live" }
                        TabButton { text: "Real Live" }
                        TabButton { text: "Real Fields" }
                        TabButton { text: "Report JSON" }
                    }
                    SmallText { text: bridge.progressText; Layout.preferredWidth: 360; horizontalAlignment: Text.AlignRight }
                }
                ProgressBar { Layout.fillWidth: true; value: bridge.progressValue / 100.0 }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: consoleTabs.currentIndex

                    ListView {
                        id: readLog
                        clip: true
                        model: bridge.readLogModel
                        ScrollBar.vertical: ScrollBar { }
                        onCountChanged: positionViewAtEnd()
                        delegate: Text { required property string text; width: readLog.width - 12; text: model.text; color: "#efefef"; font.family: "Cascadia Mono"; font.pixelSize: 11; wrapMode: Text.NoWrap }
                    }

                    ColumnLayout {
                        spacing: 4
                        TextArea { Layout.fillWidth: true; Layout.preferredHeight: 56; readOnly: true; text: bridge.realCommand; color: "#dfffffff"; font.family: "Cascadia Mono"; font.pixelSize: 10; background: Rectangle { color: "#30000000"; radius: 4 } }
                        ListView {
                            id: realLog
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.realLogModel
                            ScrollBar.vertical: ScrollBar { }
                            onCountChanged: positionViewAtEnd()
                            delegate: Text { required property string text; width: realLog.width - 12; text: model.text; color: "#efefef"; font.family: "Cascadia Mono"; font.pixelSize: 11; wrapMode: Text.NoWrap }
                        }
                    }

                    ColumnLayout {
                        spacing: 4
                        SmallText { text: bridge.realSummary; Layout.fillWidth: true }
                        Rectangle { Layout.fillWidth: true; implicitHeight: 28; color: "#18ffffff"
                            Row { anchors.fill: parent; property real unit: width/100
                                Repeater { model: [["Section",16],["Field",16],["Mode",10],["Execution",12],["Answer",20],["Persisted",10],["Detail",16]]; delegate: Text { width: parent.unit*modelData[1]; height: parent.height; text: modelData[0]; color: "#dcffffff"; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter } }
                            }
                        }
                        ListView {
                            id: realFieldsList
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.realFields
                            ScrollBar.vertical: ScrollBar { }
                            delegate: Rectangle {
                                required property var modelData
                                width: realFieldsList.width; height: 36; color: index%2 ? "#08ffffff" : "#10000000"
                                Row { anchors.fill: parent; property real unit: width/100
                                    Text { width: parent.unit*16; text: modelData.section; color: "#d8ffffff"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*16; text: modelData.field; color: "white"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*10; text: modelData.mode; color: "#c8ffffff"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*12; text: modelData.execution; color: modelData.execution.toLowerCase().indexOf("error")>=0 || modelData.execution.toLowerCase().indexOf("fail")>=0 ? "#f18da0" : "#8fe1b9"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*20; text: modelData.answer; color: "white"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*10; text: modelData.persisted; color: "#c8ffffff"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                    Text { width: parent.unit*16; text: modelData.detail; color: "#a8ffffff"; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; height: parent.height }
                                }
                            }
                        }
                    }

                    TextArea {
                        readOnly: true
                        text: bridge.realReport
                        color: "#efefef"
                        font.family: "Cascadia Mono"
                        font.pixelSize: 10
                        wrapMode: TextEdit.NoWrap
                        background: Rectangle { color: "#30000000"; radius: 4 }
                    }
                }
            }
        }
    }

    FileDialog {
        id: fileDialog
        title: "选择要上传到 Product Photos 的图片"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Images (*.jpg *.jpeg *.png *.webp)", "All files (*)"]
        onAccepted: bridge.setSelectedImages(selectedFiles)
    }

    Dialog {
        id: realConfirm
        modal: true
        anchors.centerIn: Overlay.overlay
        title: "确认真实网页操作"
        standardButtons: Dialog.Yes | Dialog.No
        width: 520
        contentItem: Text {
            padding: 18
            color: "white"
            wrapMode: Text.WordWrap
            text: "Scope: " + scopeCombo.currentText + "\n\n" +
                  (saveCheck.checked ? "Save + reopen verification" : "NO SAVE") + "\n" +
                  (uploadCheck.checked ? "上传 " + bridge.selectedImageCount + " 张图片" : "NO IMAGE UPLOAD") + "\n" +
                  "Send to QC = LOCKED / FALSE\n\n确认开始？"
        }
        background: Rectangle { color: "#f01b2532"; radius: 8; border.color: "#30ffffff" }
        onAccepted: {
            var row = bridge.realScopes[scopeCombo.currentIndex]
            bridge.startReal(row.value, saveCheck.checked, uploadCheck.checked)
        }
    }

    Dialog {
        id: noticeDialog
        property string noticeText: ""
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Ok
        width: 520
        contentItem: Text { padding: 18; text: noticeDialog.noticeText; color: "white"; wrapMode: Text.WordWrap }
        background: Rectangle { color: "#f01b2532"; radius: 8; border.color: "#30ffffff" }
    }

    Connections {
        target: bridge
        function onNotice(kind, title, message) {
            noticeDialog.title = title
            noticeDialog.noticeText = message
            noticeDialog.open()
        }
        function onFocusRealConsole() {
            consoleTabs.currentIndex = 1
        }
    }
}
