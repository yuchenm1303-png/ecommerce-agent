import QtQuick
import QtQuick.Layouts

GlassCard {
    id: root

    property string valueText: "—"
    property string titleText: ""
    property string captionText: ""
    property color valueColor: "white"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 13
        spacing: 2

        Text {
            text: root.valueText
            color: root.valueColor
            font.pixelSize: 26
            font.weight: Font.Bold
            Layout.fillWidth: true
        }
        Text {
            text: root.titleText
            color: "#f2f2f2"
            font.pixelSize: 11
            font.weight: Font.DemiBold
            Layout.fillWidth: true
        }
        Text {
            text: root.captionText
            color: "#a5ffffff"
            font.pixelSize: 10
            elide: Text.ElideRight
            Layout.fillWidth: true
        }
    }
}
