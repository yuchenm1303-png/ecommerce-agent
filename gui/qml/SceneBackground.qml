import QtQuick

Item {
    id: root

    property alias blurScene: blurScene
    property real overscan: 1.06
    property real travel: 0.90
    property real offsetX: 0
    property real offsetY: 0
    property real targetX: 0
    property real targetY: 0
    property real followerX: width / 2
    property real followerY: height / 2
    property bool followerVisible: hover.hovered

    readonly property real overscanWidth: width * overscan
    readonly property real overscanHeight: height * overscan
    readonly property real maxTravelX: Math.max(0, (overscanWidth - width) * 0.5 * travel)
    readonly property real maxTravelY: Math.max(0, (overscanHeight - height) * 0.5 * travel)

    Item {
        id: blurScene
        anchors.fill: parent
        layer.enabled: true
        layer.smooth: true

        Image {
            width: root.overscanWidth
            height: root.overscanHeight
            x: (root.width - width) * 0.5 + root.offsetX
            y: (root.height - height) * 0.5 + root.offsetY
            source: "image://wallpaper/blur"
            fillMode: Image.PreserveAspectCrop
            smooth: true
            asynchronous: false
            cache: true
        }
    }

    Image {
        id: sharpImage
        width: root.overscanWidth
        height: root.overscanHeight
        x: (root.width - width) * 0.5 + root.offsetX
        y: (root.height - height) * 0.5 + root.offsetY
        source: "image://wallpaper/sharp"
        fillMode: Image.PreserveAspectCrop
        smooth: true
        asynchronous: false
        cache: true
    }

    HoverHandler {
        id: hover
        parent: root
        target: null
        blocking: false
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    }

    FrameAnimation {
        id: animationClock
        running: root.visible
        onTriggered: {
            var px = hover.hovered ? hover.point.position.x : root.width * 0.5
            var py = hover.hovered ? hover.point.position.y : root.height * 0.5
            var nx = root.width > 0 ? Math.max(-1, Math.min(1, (px - root.width * 0.5) / (root.width * 0.5))) : 0
            var ny = root.height > 0 ? Math.max(-1, Math.min(1, (py - root.height * 0.5) / (root.height * 0.5))) : 0
            root.targetX = -nx * root.maxTravelX
            root.targetY = -ny * root.maxTravelY

            // Same 12% / 16ms response as the QWidget baseline, made time-continuous.
            var tau = -0.016 / Math.log(1.0 - 0.12)
            var dt = Math.max(0, Math.min(0.05, frameTime))
            var alpha = dt > 0 ? 1.0 - Math.exp(-dt / tau) : 0
            root.offsetX += (root.targetX - root.offsetX) * alpha
            root.offsetY += (root.targetY - root.offsetY) * alpha

            if (hover.hovered) {
                if (!root.followerVisible) {
                    root.followerX = px
                    root.followerY = py
                }
                var followAlpha = 1.0 - Math.pow(1.0 - 0.35, Math.max(0.25, dt / 0.016))
                root.followerX += (px - root.followerX) * followAlpha
                root.followerY += (py - root.followerY) * followAlpha
            }

            for (var i = 0; i < petals.count; ++i) {
                var p = petals.get(i)
                var frameScale = dt > 0 ? dt / 0.016 : 1.0
                var nxp = p.x + (0.5 * p.fnx - 1.7) * frameScale
                var nyp = p.y + p.fny * frameScale
                var nr = p.r + p.fnr * frameScale
                if (nxp > root.width || nxp < 0 || nyp > root.height || nyp < 0) {
                    if (Math.random() > 0.4) {
                        nxp = Math.random() * root.width
                        nyp = 0
                    } else {
                        nxp = root.width
                        nyp = Math.random() * root.height
                    }
                    petals.setProperty(i, "scale", Math.random())
                    nr = 6.0 * Math.random()
                }
                petals.setProperty(i, "x", nxp)
                petals.setProperty(i, "y", nyp)
                petals.setProperty(i, "r", nr)
            }
        }
    }

    ListModel {
        id: petals
        Component.onCompleted: {
            for (var i = 0; i < 3; ++i) {
                append({
                    "x": Math.random() * root.width,
                    "y": Math.random() * root.height,
                    "scale": Math.random(),
                    "r": 6.0 * Math.random(),
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
            required property real x
            required property real y
            required property real scale
            required property real r
            width: Math.max(1, 40 * scale)
            height: width
            source: "image://wallpaper/sakura"
            smooth: true
            x: model.x
            y: model.y
            rotation: model.r * 180 / Math.PI
            transformOrigin: Item.Center
        }
    }

    Rectangle {
        visible: root.followerVisible
        x: root.followerX - width / 2
        y: root.followerY - height / 2
        width: 18
        height: 18
        radius: 9
        color: Qt.rgba(1, 1, 1, 0.25)
    }
}
