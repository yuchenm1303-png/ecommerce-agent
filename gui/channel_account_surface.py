from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ChannelAccountCenterPanel(QWidget):
    """Business-state source for the shared QML detail modal.

    The QWidget never becomes the presentation owner in the normal GUI. The
    existing QuickModalLayer mirrors these controls into the one QQuickWindow.
    """

    def __init__(self, manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setObjectName("channelAccountCenterContent")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QLabel(
            "Makro 店铺与 Listing Studio 账号分开管理。每个店铺使用独立 Browser Profile + CDP lane；"
            "程序不保存 Makro 密码、Cookie 或登录 Token。"
        )
        intro.setObjectName("cardDetailText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        current_card = QFrame()
        current_card.setObjectName("cardDetailSection")
        current_layout = QVBoxLayout(current_card)
        current_layout.setContentsMargins(15, 14, 15, 15)
        current_layout.setSpacing(10)

        platform_row = QHBoxLayout()
        platform_label = QLabel("平台")
        platform_label.setObjectName("modalFieldLabel")
        platform_value = QLabel("Makro Seller Centre")
        platform_value.setObjectName("modalMetaLabel")
        platform_row.addWidget(platform_label)
        platform_row.addWidget(platform_value, 1)
        current_layout.addLayout(platform_row)

        account_row = QHBoxLayout()
        account_label = QLabel("当前店铺")
        account_label.setObjectName("modalFieldLabel")
        self.account_combo = QComboBox()
        self.account_combo.setObjectName("modalCombo")
        self.switch_button = QPushButton("切换到此店铺")
        self.switch_button.setObjectName("modalPrimaryButton")
        self.switch_button.clicked.connect(self._switch_selected)
        account_row.addWidget(account_label)
        account_row.addWidget(self.account_combo, 1)
        account_row.addWidget(self.switch_button)
        current_layout.addLayout(account_row)

        self.active_label = QLabel()
        self.active_label.setObjectName("modalMetaLabel")
        self.active_label.setWordWrap(True)
        current_layout.addWidget(self.active_label)

        self.browser_status = QLabel()
        self.browser_status.setObjectName("modalMetaLabel")
        self.browser_status.setWordWrap(True)
        current_layout.addWidget(self.browser_status)
        layout.addWidget(current_card)

        overview_card = QFrame()
        overview_card.setObjectName("cardDetailSection")
        overview_layout = QVBoxLayout(overview_card)
        overview_layout.setContentsMargins(15, 14, 15, 15)
        overview_layout.setSpacing(8)
        overview_title = QLabel("账号任务概览")
        overview_title.setObjectName("modalFieldLabel")
        overview_layout.addWidget(overview_title)
        self.account_overview = QLabel()
        self.account_overview.setObjectName("cardDetailText")
        self.account_overview.setWordWrap(True)
        self.account_overview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        overview_layout.addWidget(self.account_overview)
        layout.addWidget(overview_card)

        connect_card = QFrame()
        connect_card.setObjectName("cardDetailSection")
        connect_layout = QVBoxLayout(connect_card)
        connect_layout.setContentsMargins(15, 14, 15, 15)
        connect_layout.setSpacing(10)

        connect_title = QLabel("连接另一个 Makro 账号")
        connect_title.setObjectName("modalFieldLabel")
        connect_layout.addWidget(connect_title)

        connect_hint = QLabel(
            "创建后程序会为这个店铺分配一个全新的独立 Profile 和固定 Browser lane，并打开 Makro 官方登录页。"
            "完成一次正常登录后，该店铺会持续复用自己的会话，切换其他店铺不会覆盖它。"
        )
        connect_hint.setObjectName("cardDetailText")
        connect_hint.setWordWrap(True)
        connect_layout.addWidget(connect_hint)

        new_row = QHBoxLayout()
        self.new_label = QLineEdit()
        self.new_label.setObjectName("channelAccountLabelInput")
        self.new_label.setPlaceholderText("店铺备注，例如：Makro 主店")
        self.connect_button = QPushButton("连接新账号")
        self.connect_button.setObjectName("modalPrimaryButton")
        self.connect_button.clicked.connect(self._connect_new)
        new_row.addWidget(self.new_label, 1)
        new_row.addWidget(self.connect_button)
        connect_layout.addLayout(new_row)
        layout.addWidget(connect_card)

        policy = QLabel(
            "Single 的 Step 3 准备现场在切换店铺后会失效，避免把旧店铺页面用于新账号。"
            "每个 Makro 账号都保留自己的 Browser Profile、CDP lane 和 Batch scheduler lane。"
            "账号之间可以同时准备/真实填写；共享的供应商 Source Edge 会自动串行，避免页面互相抢占。"
        )
        policy.setObjectName("cardDetailText")
        policy.setWordWrap(True)
        layout.addWidget(policy)

        self.action_status = QLabel()
        self.action_status.setObjectName("modalMetaLabel")
        self.action_status.setWordWrap(True)
        layout.addWidget(self.action_status)
        layout.addStretch(1)

        self.manager.status_changed.connect(self._browser_status_changed)
        controller = getattr(getattr(self.manager.window, "batch_workspace", None), "controller", None)
        lane_state = getattr(controller, "lane_state_changed", None)
        if lane_state is not None:
            lane_state.connect(lambda _account_id, _snapshot: self._refresh_account_overview())
        self.reload()

    def _account_port(self, account: Any) -> int:
        getter = getattr(self.manager, "channel_browser_port", None)
        return int(getter(account)) if callable(getter) else 0

    def _refresh_account_overview(
        self,
        accounts: tuple[Any, ...] | None = None,
        current: Any | None = None,
    ) -> None:
        accounts = accounts or tuple(self.manager.list_channel_accounts())
        current = current or self.manager.channel_account
        runtime = getattr(self.manager.window, "_batch_parallel_runtime", None)
        snapshot_getter = getattr(runtime, "account_slot_snapshot", None)
        overview_lines: list[str] = []
        for account in accounts:
            port = self._account_port(account)
            marker = "●" if account.account_id == current.account_id else "○"
            batch_text = "暂无 Batch"
            if callable(snapshot_getter):
                try:
                    snapshot = snapshot_getter(account.account_id)
                except Exception as exc:
                    batch_text = f"Batch 状态不可用 · {exc}"
                else:
                    if bool(snapshot.get("has_batch")):
                        summary = snapshot.get("summary") or {}
                        status = "RUNNING" if bool(snapshot.get("running")) else str(
                            snapshot.get("status") or "IDLE"
                        )
                        batch_text = (
                            f"{status} · {int(summary.get('total') or 0)} tasks · "
                            f"{int(summary.get('ready') or 0)} ready · "
                            f"{int(summary.get('processing') or 0)} processing · "
                            f"{int(summary.get('done') or 0)} done"
                        )
            overview_lines.append(f"{marker} {account.label} · CDP {port} · {batch_text}")
        self.account_overview.setText("\n".join(overview_lines) or "暂无 Makro 账号")

    def reload(self) -> None:
        accounts = tuple(self.manager.list_channel_accounts())
        selected = self.manager.selected_channel_account()

        blocked = self.account_combo.blockSignals(True)
        try:
            self.account_combo.clear()
            selected_index = -1
            for index, account in enumerate(accounts):
                port = self._account_port(account)
                suffix = f" · CDP {port}" if port else ""
                self.account_combo.addItem(f"{account.label}{suffix}", account.account_id)
                if account.account_id == selected.account_id:
                    selected_index = index
            if selected_index >= 0:
                self.account_combo.setCurrentIndex(selected_index)
        finally:
            self.account_combo.blockSignals(blocked)

        current = self.manager.channel_account
        current_port = self._account_port(current)
        if selected.account_id == current.account_id:
            self.active_label.setText(f"上架会话 · {current.label} · CDP {current_port}")
        else:
            selected_port = self._account_port(selected)
            self.active_label.setText(
                f"目标店铺 · {selected.label} · CDP {selected_port} · "
                f"正在从 {current.label} 切换控制面"
            )

        self._refresh_account_overview(accounts, current)

        state, detail = self.manager.channel_browser_status()
        self._browser_status_changed(state, detail)
        change_blocked = getattr(self.manager, "channel_account_change_blocked", None)
        blocked = bool(change_blocked()) if callable(change_blocked) else bool(self.manager.is_busy())
        self.switch_button.setEnabled(not blocked and bool(accounts))
        self.connect_button.setEnabled(not blocked)

    def _browser_status_changed(self, state: str, detail: str) -> None:
        self.browser_status.setText(f"Browser · {str(state).upper()} · {detail}")
        state_name = str(state).upper()
        if state_name == "READY":
            self.browser_status.setStyleSheet("color: #9fe2bd; font-weight: 650;")
        elif state_name in {"ERROR", "OFFLINE", "POISONED"}:
            self.browser_status.setStyleSheet("color: #f18da0; font-weight: 650;")
        else:
            self.browser_status.setStyleSheet("color: #f4cb7a; font-weight: 650;")

    def _set_action(self, text: str, *, error: bool = False) -> None:
        self.action_status.setText(text)
        self.action_status.setStyleSheet(
            "color: #f18da0; font-weight: 650;"
            if error
            else "color: #b9d9f2; font-weight: 650;"
        )

    def _switch_selected(self) -> None:
        account_id = str(self.account_combo.currentData() or "").strip()
        if not account_id:
            return
        try:
            account = self.manager.request_activate_channel_account(account_id)
        except Exception as exc:
            self._set_action(str(exc), error=True)
            self.reload()
            return
        self._set_action(
            f"已选择 {account.label}。程序正在后台接管该店铺自己的 Makro Browser lane；"
            "完成后状态会变为 READY。其他店铺会话不会被覆盖。"
        )
        self.reload()

    def _connect_new(self) -> None:
        label = self.new_label.text().strip()
        try:
            account = self.manager.create_and_activate_channel_account(label=label)
        except Exception as exc:
            self._set_action(str(exc), error=True)
            self.reload()
            return
        self.new_label.clear()
        port = self._account_port(account)
        self._set_action(
            f"已创建 {account.label} · CDP {port}。独立 Makro Profile 正在打开；"
            "首次请在官方 Seller Centre 完成登录。"
        )
        self.reload()


class ChannelAccountCenterController:
    """Expose channel-account management through the canonical shared detail modal."""

    def __init__(self, window: QWidget, manager: Any) -> None:
        self.window = window
        self.manager = manager
        self.button = QPushButton("平台账号")
        self.button.setObjectName("quietButton")
        self.button.setToolTip("Makro 店铺连接 / 切换 / 独立 Browser lane")
        self.button.clicked.connect(self.open)
        self._panel: ChannelAccountCenterPanel | None = None
        self._install_header_button()

    def _install_header_button(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        header_item = outer.itemAt(0) if outer is not None and outer.count() else None
        header = header_item.layout() if header_item is not None else None
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("平台账号中心需要公共应用 Header。")
        header.addWidget(self.button, 0, Qt.AlignmentFlag.AlignBottom)

    def open(self) -> None:
        details = getattr(self.window, "_card_details", None)
        open_custom = getattr(details, "open_custom", None)
        body_layout = getattr(details, "body_layout", None)
        body = getattr(details, "body", None)
        if not callable(open_custom) or body_layout is None or not isinstance(body, QWidget):
            return

        def populate() -> None:
            panel = ChannelAccountCenterPanel(self.manager, body)
            self._panel = panel
            body_layout.addWidget(panel, 1)

        open_custom(
            title="平台账号",
            eyebrow="CHANNEL ACCOUNTS · MAKRO",
            populate=populate,
            ratio=(0.72, 0.72),
        )


def install_channel_account_center(
    window: QWidget,
    manager: Any,
) -> ChannelAccountCenterController:
    existing = getattr(window, "_channel_account_center", None)
    if isinstance(existing, ChannelAccountCenterController):
        return existing
    controller = ChannelAccountCenterController(window, manager)
    window._channel_account_center = controller  # type: ignore[attr-defined]
    return controller


__all__ = [
    "ChannelAccountCenterController",
    "ChannelAccountCenterPanel",
    "install_channel_account_center",
]
