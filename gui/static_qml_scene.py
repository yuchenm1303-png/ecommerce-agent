from __future__ import annotations


# No alternative theme lives here.  Glass blur stays in native_background.py;
# the values below mirror the already-installed QWidget styles exactly.
STATIC_QML_SOURCE = r'''
import QtQuick
import QtQuick.Controls

Item {
    id: staticRoot
    objectName: "staticQmlRoot"
    anchors.fill: parent
    visible: staticBridge.active
    enabled: visible
    z: 10000
    focus: visible

    function rootControl(name) {
        var controls = staticBridge.rootControls
        for (var i = 0; i < controls.length; ++i) {
            if (controls[i].name === name)
                return controls[i]
        }
        return null
    }

    property var workspaceToggleData: rootControl("workspaceModeSwitch")
    property var backgroundDriftToggleData: rootControl("backgroundDriftSwitch")

    function componentFor(kind) {
        if (kind === "label") return labelComponent
        if (kind === "badge") return badgeComponent
        if (kind === "button") return buttonComponent
        if (kind === "toggle") return toggleComponent
        if (kind === "lineedit") return lineEditComponent
        if (kind === "spinbox") return spinBoxComponent
        if (kind === "checkbox") return checkboxComponent
        if (kind === "combo") return comboComponent
        if (kind === "textedit") return textEditComponent
        if (kind === "table") return tableComponent
        if (kind === "progress") return progressComponent
        if (kind === "tabs") return tabsComponent
        if (kind === "panel") return panelComponent
        return labelComponent
    }

    function buttonFill(style, hovered, pressed, enabled) {
        if (!enabled) return Qt.rgba(0, 0, 0, 34/255)
        if (style === "primary")
            return Qt.rgba(1, 1, 1, (hovered && !pressed ? 70 : 48)/255)
        if (style === "danger")
            return Qt.rgba((hovered && !pressed ? 92 : 70)/255, 0, (hovered && !pressed ? 24 : 18)/255, (hovered && !pressed ? 118 : 86)/255)
        if (style === "quiet") return Qt.rgba(0, 0, 0, 64/255)
        if (pressed) return Qt.rgba(0, 0, 0, 78/255)
        if (hovered) return Qt.rgba(0, 0, 0, 98/255)
        return Qt.rgba(0, 0, 0, 68/255)
    }

    function buttonBorder(style, hovered, enabled) {
        if (style === "primary") return Qt.rgba(1, 1, 1, 32/255)
        return Qt.rgba(1, 1, 1, (hovered && enabled ? 32 : 16)/255)
    }

    function fieldFill(hovered, focused) {
        if (focused) return Qt.rgba(0, 0, 0, 96/255)
        if (hovered) return Qt.rgba(0, 0, 0, 86/255)
        return Qt.rgba(0, 0, 0, 72/255)
    }

    function fieldBorder(hovered, focused) {
        if (focused) return Qt.rgba(1, 1, 1, 90/255)
        if (hovered) return Qt.rgba(1, 1, 1, 44/255)
        return Qt.rgba(1, 1, 1, 26/255)
    }

    Component {
        id: labelComponent
        Item {
            property var d
            Text {
                anchors.fill: parent
                text: d ? d.text : ""
                color: d && d.color ? d.color : "white"
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                font.weight: d && d.fontWeight ? d.fontWeight : Font.Normal
                wrapMode: d && d.wordWrap ? Text.WordWrap : Text.NoWrap
                elide: d && d.wordWrap ? Text.ElideNone : Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: d && d.align === "center" ? Text.AlignHCenter : d && d.align === "right" ? Text.AlignRight : Text.AlignLeft
                textFormat: d && d.rich ? Text.RichText : Text.PlainText
            }
        }
    }

    Component {
        id: badgeComponent
        Rectangle {
            property var d
            radius: 7
            color: Qt.rgba(0, 0, 0, 72/255)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 20/255)
            Text {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                text: d ? d.text : ""
                color: d && d.color ? d.color : Qt.rgba(1, 1, 1, 225/255)
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 11
                font.weight: d && d.fontWeight ? d.fontWeight : Font.DemiBold
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: d && d.align === "center" ? Text.AlignHCenter : Text.AlignLeft
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: panelComponent
        Rectangle {
            property var d
            color: d && d.fill ? d.fill : "transparent"
            border.width: 0
            radius: 8
        }
    }

    Component {
        id: buttonComponent
        Item {
            property var d
            HoverHandler { id: buttonHover }
            TapHandler {
                id: buttonTap
                enabled: d ? d.enabled : false
                acceptedButtons: Qt.LeftButton
                onTapped: staticBridge.click(d.key)
            }
            Rectangle {
                anchors.fill: parent
                radius: 7
                color: staticRoot.buttonFill(d ? d.style : "default", buttonHover.hovered, buttonTap.pressed, d ? d.enabled : false)
                border.width: 1
                border.color: staticRoot.buttonBorder(d ? d.style : "default", buttonHover.hovered, d ? d.enabled : false)
            }
            Text {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                text: d ? d.text : ""
                color: d && d.enabled ? "white" : Qt.rgba(1, 1, 1, 76/255)
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                font.weight: d && d.fontWeight ? d.fontWeight : Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: toggleComponent
        Item {
            id: toggleRoot
            property var d
            property bool initialized: false
            property real actionPosition: 0.0

            function syncPosition() {
                if (!d)
                    return
                actionPosition = d.checked ? 1.0 : 0.0
                if (!initialized)
                    initialized = true
            }

            onDChanged: syncPosition()

            Behavior on actionPosition {
                enabled: toggleRoot.initialized
                NumberAnimation {
                    duration: 300
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.645, 0.045, 0.355, 1.0, 1.0, 1.0]
                }
            }
            TapHandler {
                acceptedButtons: Qt.LeftButton
                enabled: d ? d.enabled : false
                onTapped: staticBridge.click(d.key)
            }
            Item {
                width: 40
                height: 20
                anchors.centerIn: parent
                Rectangle { anchors.fill: parent; radius: 10; color: Qt.rgba(1, 1, 1, 48/255) }
                Text {
                    x: 2; width: 18; height: 20
                    text: "✓"; opacity: actionPosition; color: "white"
                    font.pixelSize: 11; font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
                Text {
                    x: 20; width: 18; height: 20
                    text: "×"; opacity: 1.0 - actionPosition; color: "white"
                    font.pixelSize: 11; font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
                Rectangle {
                    width: 16; height: 16; radius: 8; y: 2
                    x: 1 + 22 * actionPosition
                    color: "white"
                }
            }
        }
    }

    Component {
        id: lineEditComponent
        Item {
            property var d
            HoverHandler { id: lineHover }
            Rectangle {
                anchors.fill: parent; radius: 7
                color: staticRoot.fieldFill(lineHover.hovered, editor.activeFocus)
                border.width: 1
                border.color: staticRoot.fieldBorder(lineHover.hovered, editor.activeFocus)
            }
            Text {
                anchors.fill: parent; anchors.leftMargin: 11; anchors.rightMargin: 11
                text: d && d.placeholder ? d.placeholder : ""
                visible: editor.text.length === 0 && !editor.activeFocus
                color: Qt.rgba(1, 1, 1, 96/255)
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
            TextInput {
                id: editor
                anchors.fill: parent; leftPadding: 11; rightPadding: 11
                text: d ? d.text : ""
                readOnly: d ? d.readOnly : true
                color: "white"
                selectionColor: Qt.rgba(1, 1, 1, 58/255); selectedTextColor: "white"
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                verticalAlignment: TextInput.AlignVCenter; clip: true
                onTextEdited: if (d && !d.readOnly) staticBridge.setText(d.key, text)
            }
        }
    }

    Component {
        id: spinBoxComponent
        Item {
            property var d
            HoverHandler { id: spinHover }
            Rectangle {
                anchors.fill: parent; radius: 7
                color: staticRoot.fieldFill(spinHover.hovered, spinEditor.activeFocus)
                border.width: 1
                border.color: staticRoot.fieldBorder(spinHover.hovered, spinEditor.activeFocus)
            }
            TextInput {
                id: spinEditor
                anchors.fill: parent; leftPadding: 11; rightPadding: 11
                text: d ? String(d.value) : "0"; color: "white"
                selectionColor: Qt.rgba(1, 1, 1, 58/255); selectedTextColor: "white"
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                verticalAlignment: TextInput.AlignVCenter
                validator: IntValidator { bottom: d ? d.minimum : -2147483647; top: d ? d.maximum : 2147483647 }
                onEditingFinished: if (d) staticBridge.setValue(d.key, Number(text))
            }
        }
    }

    Component {
        id: checkboxComponent
        Item {
            property var d
            TapHandler {
                acceptedButtons: Qt.LeftButton
                enabled: d ? d.enabled : false
                onTapped: if (d) staticBridge.setChecked(d.key, !d.checked)
            }
            Rectangle {
                width: 15; height: 15; x: 0; y: (parent.height - height) / 2; radius: 4
                color: d && d.checked ? Qt.rgba(1, 1, 1, 118/255) : Qt.rgba(0, 0, 0, 72/255)
                border.width: 1
                border.color: d && d.checked ? Qt.rgba(1, 1, 1, 188/255) : Qt.rgba(1, 1, 1, 62/255)
            }
            Text {
                x: 22; width: Math.max(0, parent.width - 22); height: parent.height
                text: d ? d.text : ""; color: Qt.rgba(1, 1, 1, 205/255)
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 11
                font.weight: d && d.fontWeight ? d.fontWeight : Font.Normal
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
        }
    }

    Component {
        id: comboComponent
        ComboBox {
            id: combo
            property var d
            hoverEnabled: true
            enabled: d ? d.enabled : false
            model: d ? d.items : []
            currentIndex: d ? d.currentIndex : -1
            onActivated: if (d) staticBridge.setComboIndex(d.key, currentIndex)
            background: Rectangle {
                radius: 7
                color: staticRoot.fieldFill(combo.hovered, combo.activeFocus)
                border.width: 1
                border.color: staticRoot.fieldBorder(combo.hovered, combo.activeFocus)
            }
            contentItem: Text {
                leftPadding: 11; rightPadding: 30
                text: combo.displayText; color: "white"
                font.family: combo.d && combo.d.fontFamily ? combo.d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: combo.d && combo.d.fontSize ? combo.d.fontSize : 13
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
            indicator: Text {
                x: combo.width - width - 10; y: (combo.height - height) / 2
                text: "▾"; color: Qt.rgba(1, 1, 1, 180/255); font.pixelSize: 11
            }
            popup: Popup {
                y: combo.height + 2; width: combo.width
                implicitHeight: Math.min(contentItem.implicitHeight + 10, 260); padding: 5
                background: Rectangle {
                    color: "#3a3a3d"; radius: 7
                    border.width: 1; border.color: Qt.rgba(1, 1, 1, 28/255)
                }
                contentItem: ListView {
                    clip: true; implicitHeight: contentHeight
                    model: combo.popup.visible ? combo.delegateModel : null
                    currentIndex: combo.highlightedIndex
                }
            }
            delegate: ItemDelegate {
                width: combo.width - 10; height: 30
                highlighted: combo.highlightedIndex === index
                contentItem: Text {
                    text: modelData; color: "white"
                    font.pixelSize: combo.d && combo.d.fontSize ? combo.d.fontSize : 13
                    verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                }
                background: Rectangle { color: highlighted ? "#545458" : "transparent"; radius: 5 }
            }
        }
    }

    Component {
        id: textEditComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: d && d.name === "consoleText" ? 6 : 8
                color: Qt.rgba(0, 0, 0, (d && d.name === "consoleText" ? 76 : 74)/255)
                border.width: d && d.name === "consoleText" ? 0 : 1
                border.color: Qt.rgba(1, 1, 1, 14/255)
            }
            TextArea {
                id: area
                anchors.fill: parent
                leftPadding: 10; rightPadding: 10; topPadding: 9; bottomPadding: 9
                text: d ? d.text : ""; readOnly: d ? d.readOnly : true
                wrapMode: d && d.wrap ? TextEdit.Wrap : TextEdit.NoWrap
                color: Qt.rgba(1, 1, 1, 224/255)
                selectionColor: Qt.rgba(1, 1, 1, 48/255); selectedTextColor: "white"
                font.family: "Cascadia Mono"; font.pixelSize: d && d.fontSize ? d.fontSize : 11
                background: null
                onActiveFocusChanged: if (!activeFocus && d && !d.readOnly) staticBridge.setText(d.key, text)
            }
        }
    }

    Component {
        id: progressComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent; radius: 6
                color: Qt.rgba(0, 0, 0, 58/255)
                border.width: 1; border.color: Qt.rgba(1, 1, 1, 12/255)
                Rectangle {
                    x: 1; y: 1
                    width: Math.max(0, (parent.width - 2) * (d ? d.ratio : 0))
                    height: Math.max(0, parent.height - 2); radius: 5
                    color: Qt.rgba(1, 1, 1, 110/255)
                }
            }
            Text {
                anchors.fill: parent; text: d ? d.text : ""
                color: Qt.rgba(1, 1, 1, 220/255)
                font.pixelSize: 9; font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
        }
    }

    Component {
        id: tabsComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                visible: d && d.tabStyle === "console"
                color: Qt.rgba(0, 0, 0, 40/255); radius: 8
                border.width: 1; border.color: Qt.rgba(1, 1, 1, 14/255)
            }
            Row {
                id: tabRow
                x: 0; y: 0; height: d ? d.tabHeight : 28
                spacing: d && d.tabStyle === "side" ? 4 : 3
                Repeater {
                    model: d ? d.items : []
                    delegate: Rectangle {
                        required property string modelData
                        required property int index
                        width: d && d.tabWidths && d.tabWidths.length > index ? d.tabWidths[index] : 80
                        height: tabRow.height; radius: 7
                        color: {
                            if (!d) return "transparent"
                            if (d.tabStyle === "side") {
                                if (index === d.currentIndex) return Qt.rgba(1,1,1,36/255)
                                if (tabHover.hovered) return Qt.rgba(1,1,1,24/255)
                                return Qt.rgba(0,0,0,34/255)
                            }
                            if (index === d.currentIndex) return Qt.rgba(0,0,0,72/255)
                            if (tabHover.hovered) return Qt.rgba(0,0,0,54/255)
                            return Qt.rgba(0,0,0,34/255)
                        }
                        border.width: d && d.tabStyle === "side" ? 1 : 0
                        border.color: d && index === d.currentIndex ? Qt.rgba(1,1,1,22/255) : Qt.rgba(1,1,1,12/255)
                        HoverHandler { id: tabHover }
                        TapHandler { onTapped: if (d) staticBridge.setTabIndex(d.key, index) }
                        Text {
                            anchors.fill: parent; anchors.leftMargin: 13; anchors.rightMargin: 13
                            text: modelData
                            color: index === d.currentIndex || tabHover.hovered ? "white" : Qt.rgba(1,1,1,(d && d.tabStyle === "side" ? 158 : 150)/255)
                            font.pixelSize: 10; font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    Component {
        id: tableComponent
        Item {
            property var d
            readonly property bool consoleTable: d && d.name === "consoleTable"
            Rectangle {
                anchors.fill: parent; radius: consoleTable ? 6 : 8
                color: Qt.rgba(0, 0, 0, (consoleTable ? 62 : 58)/255)
                border.width: consoleTable ? 0 : 1
                border.color: Qt.rgba(1, 1, 1, 16/255)
            }
            Flickable {
                id: tableFlick
                anchors.fill: parent; clip: true
                contentWidth: headerRow.width
                contentHeight: (d ? d.headerHeight : 39) + rowsColumn.height
                Row {
                    id: headerRow
                    height: d ? d.headerHeight : 39
                    Repeater {
                        model: d ? d.headers : []
                        delegate: Rectangle {
                            required property string modelData
                            required property int index
                            width: d && d.columnWidths && d.columnWidths.length > index ? d.columnWidths[index] : 100
                            height: headerRow.height
                            color: Qt.rgba(1, 1, 1, (consoleTable ? 26 : 28)/255)
                            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Qt.rgba(1,1,1,20/255) }
                            Text {
                                anchors.fill: parent; leftPadding: 10; rightPadding: 10
                                text: modelData; color: Qt.rgba(1,1,1,220/255)
                                font.pixelSize: 11; font.weight: Font.Bold
                                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            }
                        }
                    }
                }
                Column {
                    id: rowsColumn
                    y: headerRow.height; width: headerRow.width
                    Repeater {
                        model: d ? d.rows : []
                        delegate: Item {
                            required property var modelData
                            required property int index
                            property var rowData: modelData
                            width: rowsColumn.width; height: d ? d.rowHeight : 40
                            Rectangle { anchors.fill: parent; color: index % 2 ? Qt.rgba(1,1,1,9/255) : "transparent" }
                            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Qt.rgba(1,1,1,10/255) }
                            Row {
                                anchors.fill: parent
                                Repeater {
                                    model: d ? d.headers.length : 0
                                    delegate: Text {
                                        required property int index
                                        width: d && d.columnWidths && d.columnWidths.length > index ? d.columnWidths[index] : 100
                                        height: parent.height; leftPadding: 10; rightPadding: 10
                                        text: rowData && rowData.length > index ? String(rowData[index]) : ""
                                        color: Qt.rgba(1,1,1,232/255); font.pixelSize: 11
                                        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Repeater {
        model: staticCardModel
        delegate: Item {
            id: card
            required property int index
            required property real cardX
            required property real cardY
            required property real cardW
            required property real cardH
            required property string cardName
            required property real hoverScale
            required property var cardControls

            x: cardX; y: cardY; width: cardW; height: cardH
            transformOrigin: Item.Center
            scale: cardClick.pressed ? 1.0 : cardHover.hovered ? hoverScale : 1.0
            Behavior on scale {
                NumberAnimation {
                    duration: 300
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.25, 0.10, 0.25, 1.00, 1.00, 1.00]
                }
            }

            // Keep the known-smooth architecture: native_background.py owns the
            // cached blur, while this card owns only its tone, content and GPU
            // transform. Scroll viewport clipping is applied only to flattened
            // child controls below; the card itself remains one unclipped GPU item.
            Rectangle {
                anchors.fill: parent
                radius: 6
                color: "#56354E"
                opacity: (cardHover.hovered || cardClick.pressed) ? 102/255 : 64/255
                Behavior on opacity {
                    NumberAnimation {
                        duration: 300
                        easing.type: Easing.BezierSpline
                        easing.bezierCurve: [0.25, 0.10, 0.25, 1.00, 1.00, 1.00]
                    }
                }
            }

            MouseArea {
                id: cardClick
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                onClicked: staticBridge.requestCardDetail(card.index)
            }

            Repeater {
                model: card.cardControls
                delegate: Item {
                    required property var modelData
                    property var d: modelData
                    readonly property bool clipped: d && d.clipEnabled
                    x: clipped ? d.clipX : d.x
                    y: clipped ? d.clipY : d.y
                    width: clipped ? d.clipW : d.w
                    height: clipped ? d.clipH : d.h
                    clip: clipped

                    Loader {
                        property var controlData: parent.d
                        x: parent.clipped ? controlData.x - controlData.clipX : 0
                        y: parent.clipped ? controlData.y - controlData.clipY : 0
                        width: controlData ? controlData.w : 0
                        height: controlData ? controlData.h : 0
                        sourceComponent: controlData ? staticRoot.componentFor(controlData.kind) : null
                        onLoaded: if (item) item.d = controlData
                        onControlDataChanged: if (item) item.d = controlData
                    }
                }
            }

            HoverHandler { id: cardHover; acceptedDevices: PointerDevice.Mouse }
        }
    }

    Repeater {
        model: staticBridge.rootControls
        delegate: Item {
            required property var modelData
            property var d: modelData
            readonly property bool clipped: d && d.clipEnabled
            x: clipped ? d.clipX : d.x
            y: clipped ? d.clipY : d.y
            width: clipped ? d.clipW : d.w
            height: clipped ? d.clipH : d.h
            clip: clipped

            Loader {
                property var controlData: parent.d
                x: parent.clipped ? controlData.x - controlData.clipX : 0
                y: parent.clipped ? controlData.y - controlData.clipY : 0
                width: controlData ? controlData.w : 0
                height: controlData ? controlData.h : 0
                sourceComponent: controlData && controlData.kind !== "toggle" ? staticRoot.componentFor(controlData.kind) : null
                onLoaded: if (item) item.d = controlData
                onControlDataChanged: if (item) item.d = controlData
            }
        }
    }

    // The two header switches are global controls, not workspace content. Keep
    // each Loader alive for the entire Quick scene lifetime so refreshing one
    // switch cannot destroy/recreate the other and replay its initialization.
    Loader {
        id: workspaceToggleLoader
        property var d: staticRoot.workspaceToggleData
        x: d ? d.x : 0; y: d ? d.y : 0
        width: d ? d.w : 0; height: d ? d.h : 0
        visible: d !== null
        sourceComponent: toggleComponent
        onLoaded: if (item) item.d = d
        onDChanged: if (item) item.d = d
    }

    Loader {
        id: backgroundDriftToggleLoader
        property var d: staticRoot.backgroundDriftToggleData
        x: d ? d.x : 0; y: d ? d.y : 0
        width: d ? d.w : 0; height: d ? d.h : 0
        visible: d !== null
        sourceComponent: toggleComponent
        onLoaded: if (item) item.d = d
        onDChanged: if (item) item.d = d
    }

    Repeater {
        model: 6
        delegate: Item {
            id: petal
            required property int index
            property real s: Math.random()
            property real r: 6.0 * Math.random()
            property real fnx: Math.random() - 0.5
            property real fny: 1.5 + 0.7 * Math.random()
            property real fnr: 0.03 * Math.random()
            x: Math.random() * Math.max(1, staticRoot.width)
            y: Math.random() * Math.max(1, staticRoot.height)
            width: Math.max(1, Math.min(40, Math.round(40 * s)))
            height: width; z: 19000
            Image { anchors.fill: parent; source: staticBridge.sakuraUrl; smooth: false; rotation: petal.r * 57.295779513 }
            function respawn() {
                if (Math.random() > 0.4) { petal.x = Math.random() * Math.max(1, staticRoot.width); petal.y = 0 }
                else { petal.x = staticRoot.width; petal.y = Math.random() * Math.max(1, staticRoot.height) }
                petal.s = Math.random(); petal.r = 6.0 * Math.random()
            }
            FrameAnimation {
                running: staticRoot.visible
                onTriggered: {
                    petal.x += 0.5 * petal.fnx - 1.7
                    petal.y += petal.fny
                    petal.r += petal.fnr
                    if (petal.x > staticRoot.width || petal.x < 0 || petal.y > staticRoot.height || petal.y < 0) petal.respawn()
                }
            }
        }
    }

    HoverHandler { id: pointerHover; acceptedDevices: PointerDevice.Mouse }
    TapHandler { id: pointerPress; target: null; acceptedButtons: Qt.LeftButton }

    Item {
        id: cursorState
        property real currentX: pointerHover.point.position.x
        property real currentY: pointerHover.point.position.y
        property real targetX: pointerHover.point.position.x
        property real targetY: pointerHover.point.position.y
        property bool initialized: false
        FrameAnimation {
            running: staticRoot.visible && pointerHover.hovered
            onTriggered: {
                cursorState.targetX = pointerHover.point.position.x
                cursorState.targetY = pointerHover.point.position.y
                if (!cursorState.initialized) {
                    cursorState.currentX = cursorState.targetX; cursorState.currentY = cursorState.targetY; cursorState.initialized = true
                }
                var dx = cursorState.targetX - cursorState.currentX
                var dy = cursorState.targetY - cursorState.currentY
                if (Math.sqrt(dx*dx + dy*dy) <= 0.35) {
                    cursorState.currentX = cursorState.targetX; cursorState.currentY = cursorState.targetY
                } else {
                    cursorState.currentX += dx * 0.35; cursorState.currentY += dy * 0.35
                }
            }
        }
    }

    Rectangle {
        readonly property real diameter: pointerPress.pressed ? 9.0 : 18.0
        width: diameter; height: diameter; radius: diameter / 2
        x: cursorState.currentX - width / 2; y: cursorState.currentY - height / 2
        color: Qt.rgba(1, 1, 1, pointerPress.pressed ? 128/255 : 64/255)
        visible: staticRoot.visible && pointerHover.hovered; z: 20000
    }
}
'''


__all__ = ["STATIC_QML_SOURCE"]
