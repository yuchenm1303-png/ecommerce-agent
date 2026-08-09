import QtQuick

Item {
    id: root

    property Item maskLayer
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

    // Each card contributes geometry only. The entire blurred wallpaper is
    // composited once by SceneBackground through one shared mask layer.
    Rectangle {
        parent: root.maskLayer
        x: root.sceneX
        y: root.sceneY
        width: root.width
        height: root.height
        radius: root.radius
        color: "white"
        visible: root.visible && root.width > 0 && root.height > 0
        antialiasing: true
    }

    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: Qt.rgba(0, 0, 0, root.tintAlpha / 255.0)

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
