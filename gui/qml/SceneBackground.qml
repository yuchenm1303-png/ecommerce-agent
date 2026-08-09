import QtQuick
import QtQuick.Effects

Item {
    id: root

    property alias glassMaskLayer: glassMaskLayer
    property real overscan: 1.06
    property real travel: 0.90
    property real offsetX: 0
    property real offsetY: 0
    property real targetX: 0
    property real targetY: 0

    readonly property real overscanWidth: width * overscan
    readonly property real overscanHeight: height * overscan
    readonly property real maxTravelX: Math.max(0, (overscanWidth - width) * 0.5 * travel)
    readonly property real maxTravelY: Math.max(0, (overscanHeight - height) * 0.5 * travel)

    // One full-window pre-blurred source texture. MultiEffect can consume a
    // layered source directly, avoiding a proxy ShaderEffectSource per card.
    Item {
        id: blurSource
        anchors.fill: parent
        visible: false
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

    // Every GlassCard contributes one cheap rounded rectangle here. The layer
    // becomes a single alpha texture for the whole window and changes only when
    // card geometry changes, not when the wallpaper moves.
    Item {
        id: glassMaskLayer
        anchors.fill: parent
        visible: false
        layer.enabled: true
        layer.smooth: true
    }

    MultiEffect {
        anchors.fill: parent
        source: blurSource
        autoPaddingEnabled: false
        maskEnabled: true
        maskSource: glassMaskLayer
        maskSpreadAtMin: 0.015
        maskSpreadAtMax: 0.015
    }

    HoverHandler {
        id: hover
        parent: root
        target: null
        blocking: false
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    }

    FrameAnimation {
        running: root.visible
        onTriggered: {
            var px = hover.hovered ? hover.point.position.x : root.width * 0.5
            var py = hover.hovered ? hover.point.position.y : root.height * 0.5
            var nx = root.width > 0 ? Math.max(-1, Math.min(1, (px - root.width * 0.5) / (root.width * 0.5))) : 0
            var ny = root.height > 0 ? Math.max(-1, Math.min(1, (py - root.height * 0.5) / (root.height * 0.5))) : 0
            root.targetX = -nx * root.maxTravelX
            root.targetY = -ny * root.maxTravelY

            // Preserve the original 12% / 16ms response while making it
            // refresh-rate independent and synchronized with scene frames.
            var tau = -0.016 / Math.log(1.0 - 0.12)
            var dt = Math.max(0, Math.min(0.05, frameTime))
            var alpha = dt > 0 ? 1.0 - Math.exp(-dt / tau) : 0
            root.offsetX += (root.targetX - root.offsetX) * alpha
            root.offsetY += (root.targetY - root.offsetY) * alpha
        }
    }
}
