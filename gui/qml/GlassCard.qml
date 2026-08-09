import QtQuick
import QtQuick.Effects

Item {
    id: root

    property Item blurScene
    property real sceneX: 0
    property real sceneY: 0
    property real radius: 6
    property bool interactive: true
    property real normalAlpha: 64
    property real hoverAlpha: 82
    property real activeAlpha: 96
    property alias contentItem: content
    default property alias contentData: content.data

    property real tintAlpha: tap.pressed ? activeAlpha : (hover.hovered ? hoverAlpha : normalAlpha)

    ShaderEffectSource {
        id: blurSample
        anchors.fill: parent
        sourceItem: root.blurScene
        sourceRect: Qt.rect(root.sceneX, root.sceneY, root.width, root.height)
        live: true
        recursive: false
        hideSource: false
        smooth: true
    }

    Rectangle {
        id: roundedMask
        anchors.fill: parent
        radius: root.radius
        color: "white"
        visible: false
        layer.enabled: true
    }

    MultiEffect {
        anchors.fill: parent
        source: blurSample
        autoPaddingEnabled: false
        maskEnabled: true
        maskSource: roundedMask
        maskSpreadAtMin: 0.015
        maskSpreadAtMax: 0.015
    }

    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: Qt.rgba(0, 0, 0, root.tintAlpha / 255.0)
        border.width: 0

        Behavior on color {
            ColorAnimation { duration: tap.pressed ? 80 : 120 }
        }
    }

    HoverHandler {
        id: hover
        enabled: root.interactive
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    }

    TapHandler {
        id: tap
        enabled: root.interactive
        acceptedButtons: Qt.LeftButton
    }

    Item {
        id: content
        anchors.fill: parent
    }
}
