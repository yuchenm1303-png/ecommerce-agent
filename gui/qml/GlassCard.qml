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

    function intersectRects(a, b) {
        var left = Math.max(a.x, b.x)
        var top = Math.max(a.y, b.y)
        var right = Math.min(a.x + a.width, b.x + b.width)
        var bottom = Math.min(a.y + a.height, b.y + b.height)
        return Qt.rect(left, top, Math.max(0, right - left), Math.max(0, bottom - top))
    }

    function effectiveClipRect() {
        if (!root.maskLayer || root.width <= 0 || root.height <= 0)
            return Qt.rect(0, 0, 0, 0)

        // Start with the card in the global mask coordinate system, then carry
        // the real QML ancestor clip chain across the re-parenting boundary.
        var result = Qt.rect(root.sceneX, root.sceneY, root.width, root.height)
        var ancestor = root.parent
        while (ancestor && ancestor !== root.maskLayer && result.width > 0 && result.height > 0) {
            if (ancestor.clip === true) {
                var mapped = ancestor.mapToItem(root.maskLayer, 0, 0)
                result = root.intersectRects(
                    result,
                    Qt.rect(mapped.x, mapped.y, ancestor.width, ancestor.height)
                )
            }
            ancestor = ancestor.parent
        }
        return result
    }

    // Each card contributes geometry only. The proxy is clipped exactly like
    // the visible card, including ScrollView/ListView ancestor viewports, while
    // all cards still collapse into one shared mask texture.
    Item {
        id: maskProxy
        parent: root.maskLayer
        property rect effectiveRect: root.effectiveClipRect()
        x: effectiveRect.x
        y: effectiveRect.y
        width: effectiveRect.width
        height: effectiveRect.height
        clip: true
        visible: root.visible && width > 0 && height > 0

        Rectangle {
            x: root.sceneX - maskProxy.x
            y: root.sceneY - maskProxy.y
            width: root.width
            height: root.height
            radius: root.radius
            color: "white"
            antialiasing: true
        }
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
