from __future__ import annotations


STATIC_QML_SOURCE = r'''
import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: staticRoot
    objectName: "staticQmlRoot"
    anchors.fill: parent
    visible: staticBridge.active
    enabled: visible
    z: 10000
    focus: visible

    readonly property real wallpaperScale: 1.06

    function componentFor(kind) {
        if (kind === "label") return labelComponent
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

    function cardFill(name) {
        if (name === "heroCard") return Qt.rgba(96/255, 58/255, 88/255, 120/255)
        if (name === "statusCard") return Qt.rgba(104/255, 66/255, 94/255, 128/255)
        if (name === "microCard") return Qt.rgba(54/255, 40/255, 61/255, 112/255)
        return Qt.rgba(86/255, 53/255, 78/255, 142/255)
    }

    function cardBorder(name) {
        if (name === "heroCard") return Qt.rgba(255/255, 239/255, 249/255, 42/255)
        if (name === "statusCard") return Qt.rgba(255/255, 241/255, 249/255, 38/255)
        if (name === "microCard") return Qt.rgba(255/255, 242/255, 249/255, 28/255)
        return Qt.rgba(255/255, 238/255, 248/255, 40/255)
    }

    function cardRadius(name) {
        if (name === "heroCard") return 22
        if (name === "statusCard") return 17
        if (name === "microCard") return 14
        return 18
    }

    function buttonFill(style, hovered, pressed, enabled) {
        if (!enabled) return Qt.rgba(63/255, 48/255, 65/255, 68/255)
        if (style === "primary") {
            if (pressed) return Qt.rgba(172/255, 94/255, 140/255, 210/255)
            if (hovered) return Qt.rgba(211/255, 132/255, 178/255, 220/255)
            return Qt.rgba(190/255, 113/255, 157/255, 190/255)
        }
        if (style === "danger") return pressed ? Qt.rgba(112/255, 48/255, 66/255, 160/255) : Qt.rgba(131/255, 64/255, 79/255, 125/255)
        if (style === "quiet") return hovered ? Qt.rgba(91/255, 62/255, 88/255, 128/255) : Qt.rgba(61/255, 45/255, 66/255, 92/255)
        if (pressed) return Qt.rgba(68/255, 44/255, 66/255, 154/255)
        if (hovered) return Qt.rgba(119/255, 76/255, 103/255, 145/255)
        return Qt.rgba(74/255, 52/255, 75/255, 112/255)
    }

    Component {
        id: labelComponent
        Item {
            property var d
            Text {
                anchors.fill: parent
                text: d ? d.text : ""
                color: d && d.color ? d.color : "#fff7fb"
                font.family: d && d.fontFamily ? d.fontFamily : "Segoe UI"
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
        id: panelComponent
        Rectangle {
            property var d
            color: d && d.fill ? d.fill : Qt.rgba(0, 0, 0, .12)
            border.width: d && d.border ? 1 : 0
            border.color: d && d.border ? d.border : "transparent"
            radius: d && d.radius ? d.radius : 8
        }
    }

    Component {
        id: buttonComponent
        Item {
            property var d
            HoverHandler { id: h }
            TapHandler {
                id: tap
                enabled: d ? d.enabled : false
                acceptedButtons: Qt.LeftButton
                onTapped: staticBridge.click(d.key)
            }
            Rectangle {
                anchors.fill: parent
                radius: 11
                color: staticRoot.buttonFill(d ? d.style : "default", h.hovered, tap.pressed, d ? d.enabled : false)
                border.width: 1
                border.color: d && d.style === "primary" ? Qt.rgba(255/255,220/255,239/255,105/255) : Qt.rgba(255/255,239/255,248/255,40/255)
            }
            Text {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                text: d ? d.text : ""
                color: d && d.enabled ? "#fff9fc" : Qt.rgba(255/255,246/255,250/255,78/255)
                font.family: "Segoe UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                font.weight: d && d.style === "primary" ? Font.DemiBold : Font.Normal
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: toggleComponent
        Item {
            property var d
            property bool checked: d ? d.checked : false
            TapHandler {
                acceptedButtons: Qt.LeftButton
                enabled: d ? d.enabled : false
                onTapped: staticBridge.click(d.key)
            }
            Rectangle {
                width: 40
                height: 20
                anchors.centerIn: parent
                radius: 10
                color: Qt.rgba(1,1,1,.19)
                Text {
                    x: 2
                    width: 18
                    height: parent.height
                    text: "✓"
                    visible: checked
                    color: "white"
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                Text {
                    x: 20
                    width: 18
                    height: parent.height
                    text: "×"
                    visible: !checked
                    color: "white"
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                Rectangle {
                    width: 16
                    height: 16
                    radius: 8
                    y: 2
                    x: checked ? 23 : 1
                    color: "white"
                    Behavior on x {
                        NumberAnimation {
                            duration: 300
                            easing.type: Easing.BezierSpline
                            easing.bezierCurve: [0.645, 0.045, 0.355, 1.0, 1.0, 1.0]
                        }
                    }
                }
            }
        }
    }

    Component {
        id: lineEditComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: 11
                color: input.activeFocus ? Qt.rgba(45/255,30/255,49/255,125/255) : Qt.rgba(39/255,28/255,44/255,90/255)
                border.width: 1
                border.color: input.activeFocus ? Qt.rgba(255/255,211/255,235/255,142/255) : Qt.rgba(255/255,239/255,248/255,38/255)
            }
            Text {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                text: d && !input.text ? d.placeholder : ""
                color: Qt.rgba(1,1,1,.34)
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            TextInput {
                id: input
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                text: d ? d.text : ""
                color: "#fffdfd"
                enabled: d ? d.enabled : false
                readOnly: d ? d.readOnly : false
                clip: true
                selectByMouse: true
                verticalAlignment: TextInput.AlignVCenter
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                onEditingFinished: staticBridge.setText(d.key, text)
            }
        }
    }

    Component {
        id: spinBoxComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: 11
                color: Qt.rgba(39/255,28/255,44/255,90/255)
                border.width: 1
                border.color: Qt.rgba(255/255,239/255,248/255,38/255)
            }
            TextInput {
                id: numberInput
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                text: d ? String(d.value) : ""
                color: "#fffdfd"
                enabled: d ? d.enabled : false
                inputMethodHints: Qt.ImhDigitsOnly
                verticalAlignment: TextInput.AlignVCenter
                validator: IntValidator { bottom: d ? d.minimum : -2147483647; top: d ? d.maximum : 2147483647 }
                onEditingFinished: staticBridge.setValue(d.key, Number(text))
            }
        }
    }

    Component {
        id: checkboxComponent
        Item {
            property var d
            TapHandler {
                enabled: d ? d.enabled : false
                acceptedButtons: Qt.LeftButton
                onTapped: staticBridge.setChecked(d.key, !d.checked)
            }
            Rectangle {
                x: 0
                y: Math.max(0, (parent.height - 17) / 2)
                width: 17
                height: 17
                radius: 5
                color: d && d.checked ? "#c479a7" : Qt.rgba(47/255,34/255,49/255,92/255)
                border.width: 1
                border.color: d && d.checked ? "#f5cce3" : Qt.rgba(255/255,243/255,249/255,65/255)
                Text {
                    anchors.centerIn: parent
                    text: d && d.checked ? "✓" : ""
                    color: "white"
                    font.pixelSize: 11
                }
            }
            Text {
                x: 25
                width: Math.max(0, parent.width - 25)
                height: parent.height
                text: d ? d.text : ""
                color: Qt.rgba(255/255,244/255,249/255,210/255)
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
            }
        }
    }

    Component {
        id: comboComponent
        ComboBox {
            property var d
            model: d ? d.items : []
            currentIndex: d ? d.currentIndex : -1
            enabled: d ? d.enabled : false
            onActivated: staticBridge.setComboIndex(d.key, currentIndex)
            contentItem: Text {
                leftPadding: 12
                rightPadding: 24
                text: parent.displayText
                color: "#fffdfd"
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
            }
            background: Rectangle {
                radius: 11
                color: Qt.rgba(39/255,28/255,44/255,90/255)
                border.width: 1
                border.color: Qt.rgba(255/255,239/255,248/255,38/255)
            }
        }
    }

    Component {
        id: textEditComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: 13
                color: Qt.rgba(29/255,24/255,36/255,106/255)
                border.width: 1
                border.color: Qt.rgba(255/255,238/255,248/255,22/255)
            }
            ScrollView {
                anchors.fill: parent
                anchors.margins: 6
                clip: true
                TextArea {
                    text: d ? d.text : ""
                    color: "#f3eaf0"
                    readOnly: d ? d.readOnly : true
                    enabled: d ? d.enabled : false
                    wrapMode: d && d.wrap ? TextEdit.Wrap : TextEdit.NoWrap
                    selectByMouse: true
                    background: null
                    font.family: d && d.mono ? "Cascadia Mono" : "Segoe UI"
                    font.pixelSize: d && d.fontSize ? d.fontSize : 12
                    onEditingFinished: if (d && !d.readOnly) staticBridge.setText(d.key, text)
                }
            }
        }
    }

    Component {
        id: progressComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: Math.max(1, height / 2)
                color: Qt.rgba(1,1,1,.095)
                Rectangle {
                    width: parent.width * (d ? d.ratio : 0)
                    height: parent.height
                    radius: parent.radius
                    color: d && d.chunkColor ? d.chunkColor : Qt.rgba(150/255,220/255,255/255,190/255)
                }
            }
        }
    }

    Component {
        id: tabsComponent
        Item {
            property var d
            Row {
                anchors.fill: parent
                spacing: 4
                Repeater {
                    model: d ? d.items : []
                    delegate: Rectangle {
                        required property string modelData
                        required property int index
                        width: Math.max(1, (parent.width - Math.max(0, (d.items.length - 1) * parent.spacing)) / Math.max(1, d.items.length))
                        height: parent.height
                        radius: 8
                        color: index === d.currentIndex ? Qt.rgba(1,1,1,.165) : Qt.rgba(0,0,0,.12)
                        border.width: 1
                        border.color: index === d.currentIndex ? Qt.rgba(1,1,1,.11) : Qt.rgba(1,1,1,.05)
                        Text {
                            anchors.fill: parent
                            text: modelData
                            color: index === d.currentIndex ? "white" : Qt.rgba(1,1,1,.67)
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                        TapHandler { onTapped: staticBridge.setTabIndex(d.key, index) }
                    }
                }
            }
        }
    }

    Component {
        id: tableComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent
                radius: 13
                color: Qt.rgba(35/255,28/255,42/255,96/255)
                border.width: 1
                border.color: Qt.rgba(255/255,238/255,248/255,24/255)
            }
            Column {
                anchors.fill: parent
                anchors.margins: 1
                Rectangle {
                    width: parent.width
                    height: 30
                    color: Qt.rgba(101/255,68/255,94/255,105/255)
                    Row {
                        anchors.fill: parent
                        Repeater {
                            model: d ? d.headers : []
                            delegate: Text {
                                required property string modelData
                                width: parent.width / Math.max(1, d.headers.length)
                                height: parent.height
                                leftPadding: 8
                                rightPadding: 8
                                text: modelData
                                color: Qt.rgba(255/255,246/255,251/255,218/255)
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                            }
                        }
                    }
                }
                Flickable {
                    width: parent.width
                    height: Math.max(0, parent.height - 30)
                    clip: true
                    contentWidth: width
                    contentHeight: rowsColumn.height
                    Column {
                        id: rowsColumn
                        width: parent.width
                        Repeater {
                            model: d ? d.rows : []
                            delegate: Rectangle {
                                required property var modelData
                                required property int index
                                width: rowsColumn.width
                                height: 28
                                color: index % 2 ? Qt.rgba(1,1,1,.035) : "transparent"
                                Row {
                                    anchors.fill: parent
                                    Repeater {
                                        model: d ? d.headers.length : 0
                                        delegate: Text {
                                            required property int index
                                            width: parent.width / Math.max(1, d.headers.length)
                                            height: parent.height
                                            leftPadding: 8
                                            rightPadding: 8
                                            text: modelData && modelData.length > index ? String(modelData[index]) : ""
                                            color: "#fff8fc"
                                            verticalAlignment: Text.AlignVCenter
                                            elide: Text.ElideRight
                                            font.pixelSize: 11
                                        }
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
            required property real cardX
            required property real cardY
            required property real cardW
            required property real cardH
            required property string cardName
            required property var cardControls

            x: cardX
            y: cardY
            width: cardW
            height: cardH
            transformOrigin: Item.Center
            scale: ((cardHover.point.pressedButtons & Qt.LeftButton) !== 0) ? 1.0 : cardHover.hovered ? 1.02 : 1.0

            Behavior on scale {
                NumberAnimation {
                    duration: 300
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.25, 0.10, 0.25, 1.00, 1.00, 1.00]
                }
            }

            Item {
                id: blurSource
                anchors.fill: parent
                visible: false
                layer.enabled: true
                layer.smooth: true
                Image {
                    width: staticRoot.width * staticRoot.wallpaperScale
                    height: staticRoot.height * staticRoot.wallpaperScale
                    x: (staticRoot.width - width) / 2 - card.x
                    y: (staticRoot.height - height) / 2 - card.y
                    source: staticBridge.blurUrl
                    fillMode: Image.PreserveAspectCrop
                    smooth: true
                    cache: true
                }
            }

            Rectangle {
                id: roundMask
                anchors.fill: parent
                radius: staticRoot.cardRadius(card.cardName)
                visible: false
                color: "white"
                layer.enabled: true
            }

            MultiEffect {
                anchors.fill: parent
                source: blurSource
                maskEnabled: true
                maskSource: roundMask
                autoPaddingEnabled: false
            }

            Rectangle {
                anchors.fill: parent
                radius: staticRoot.cardRadius(card.cardName)
                color: staticRoot.cardFill(card.cardName)
            }

            Rectangle {
                anchors.fill: parent
                radius: staticRoot.cardRadius(card.cardName)
                color: "black"
                opacity: (cardHover.hovered || ((cardHover.point.pressedButtons & Qt.LeftButton) !== 0)) ? 102/255 : 64/255
                Behavior on opacity {
                    NumberAnimation {
                        duration: 300
                        easing.type: Easing.BezierSpline
                        easing.bezierCurve: [0.25, 0.10, 0.25, 1.00, 1.00, 1.00]
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                radius: staticRoot.cardRadius(card.cardName)
                color: "transparent"
                border.width: 1
                border.color: staticRoot.cardBorder(card.cardName)
            }

            Repeater {
                model: card.cardControls
                delegate: Loader {
                    required property var modelData
                    property var d: modelData
                    x: d.x
                    y: d.y
                    width: d.w
                    height: d.h
                    sourceComponent: staticRoot.componentFor(d.kind)
                    onLoaded: if (item) item.d = d
                }
            }

            HoverHandler {
                id: cardHover
                acceptedDevices: PointerDevice.Mouse
            }
        }
    }

    Repeater {
        model: staticBridge.rootControls
        delegate: Loader {
            required property var modelData
            property var d: modelData
            x: d.x
            y: d.y
            width: d.w
            height: d.h
            sourceComponent: staticRoot.componentFor(d.kind)
            onLoaded: if (item) item.d = d
        }
    }

    Repeater {
        model: 3
        delegate: Image {
            required property int index
            source: staticBridge.sakuraUrl
            width: 18 + index * 5
            height: width
            smooth: true
            opacity: .82
            x: staticRoot.width * ([.18, .52, .82][index])
            y: staticRoot.height * ([.08, .34, .16][index])
            rotation: index * 37
            visible: staticRoot.visible
            SequentialAnimation on y {
                running: staticRoot.visible
                loops: Animation.Infinite
                NumberAnimation { to: staticRoot.height + 40; duration: 9800 + index * 1700; easing.type: Easing.Linear }
                PropertyAction { value: -40 }
            }
            SequentialAnimation on x {
                running: staticRoot.visible
                loops: Animation.Infinite
                NumberAnimation { to: -40; duration: 12800 + index * 1100; easing.type: Easing.Linear }
                PropertyAction { value: staticRoot.width + 40 }
            }
            NumberAnimation on rotation {
                running: staticRoot.visible
                loops: Animation.Infinite
                from: index * 37
                to: index * 37 + 360
                duration: 15000 + index * 2400
            }
        }
    }

    HoverHandler {
        id: pointerHover
        acceptedDevices: PointerDevice.Mouse
    }

    Rectangle {
        id: cursorFollow
        width: ((pointerHover.point.pressedButtons & Qt.LeftButton) !== 0) ? 9 : 18
        height: width
        radius: width / 2
        x: pointerHover.point.position.x - width / 2
        y: pointerHover.point.position.y - height / 2
        color: Qt.rgba(1, 1, 1, ((pointerHover.point.pressedButtons & Qt.LeftButton) !== 0) ? .50 : .25)
        visible: staticRoot.visible && pointerHover.hovered
        z: 20000
        Behavior on x { NumberAnimation { duration: 70; easing.type: Easing.OutQuad } }
        Behavior on y { NumberAnimation { duration: 70; easing.type: Easing.OutQuad } }
        Behavior on width { NumberAnimation { duration: 120; easing.type: Easing.InOutQuad } }
        Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.InOutQuad } }
    }
}
'''


__all__ = ["STATIC_QML_SOURCE"]
