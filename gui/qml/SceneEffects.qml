import QtQuick

Item {
    id: root

    property real followerX: width / 2
    property real followerY: height / 2
    property bool followerInitialized: false

    HoverHandler {
        id: hover
        parent: root
        target: null
        blocking: false
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    }

    PointHandler {
        id: pointerPress
        target: null
        acceptedButtons: Qt.LeftButton
    }

    FrameAnimation {
        running: root.visible
        onTriggered: {
            var dt = Math.max(0, Math.min(0.05, frameTime))
            var frameScale = dt > 0 ? dt / 0.016 : 1.0

            if (hover.hovered) {
                var px = hover.point.position.x
                var py = hover.point.position.y
                if (!root.followerInitialized) {
                    root.followerX = px
                    root.followerY = py
                    root.followerInitialized = true
                } else {
                    var followAlpha = 1.0 - Math.pow(1.0 - 0.35, Math.max(0.25, frameScale))
                    root.followerX += (px - root.followerX) * followAlpha
                    root.followerY += (py - root.followerY) * followAlpha
                }
            }

            for (var i = 0; i < petals.count; ++i) {
                var p = petals.get(i)
                var nextX = p.px + (0.5 * p.fnx - 1.7) * frameScale
                var nextY = p.py + p.fny * frameScale
                var nextR = p.rot + p.fnr * frameScale
                if (nextX > root.width || nextX < 0 || nextY > root.height || nextY < 0) {
                    if (Math.random() > 0.4) {
                        nextX = Math.random() * root.width
                        nextY = 0
                    } else {
                        nextX = root.width
                        nextY = Math.random() * root.height
                    }
                    petals.setProperty(i, "petalScale", Math.random())
                    nextR = 6.0 * Math.random()
                }
                petals.setProperty(i, "px", nextX)
                petals.setProperty(i, "py", nextY)
                petals.setProperty(i, "rot", nextR)
            }
        }
    }

    ListModel {
        id: petals
        Component.onCompleted: {
            for (var i = 0; i < 3; ++i) {
                append({
                    "px": Math.random() * root.width,
                    "py": Math.random() * root.height,
                    "petalScale": Math.random(),
                    "rot": 6.0 * Math.random(),
                    "fnx": Math.random() - 0.5,
                    "fny": 1.5 + 0.7 * Math.random(),
                    "fnr": 0.03 * Math.random()
                })
            }
        }
    }

    Repeater {
        model: petals
        delegate: Image {
            width: Math.max(1, Math.min(40, Math.round(40 * petalScale)))
            height: width
            x: px
            y: py
            source: "image://wallpaper/sakura"
            smooth: true
            rotation: rot * 180 / Math.PI
            transformOrigin: Item.TopLeft
        }
    }

    Rectangle {
        visible: hover.hovered && root.followerInitialized
        x: root.followerX - width / 2
        y: root.followerY - height / 2
        width: pointerPress.active ? 9 : 18
        height: width
        radius: width / 2
        color: pointerPress.active ? Qt.rgba(1, 1, 1, 0.50) : Qt.rgba(1, 1, 1, 0.25)
    }
}
