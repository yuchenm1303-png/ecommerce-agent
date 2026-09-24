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
        self._account_cards: dict[str, dict[str, Any]] = {}

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

        current_row = QHBoxLayout()
        current_identity = QVBoxLayout()
        current_identity.setSpacing(2)
        self.current_store_name = QLabel("等待 Makro 店铺")
        self.current_store_name.setObjectName("cardTitle")
        self.current_store_meta = QLabel("独立 Browser Profile · 独立 CDP lane")
        self.current_store_meta.setObjectName("modalMetaLabel")
        current_identity.addWidget(self.current_store_name)
        current_identity.addWidget(self.current_store_meta)
        current_row.addLayout(current_identity, 1)

        self.fleet_badge = QLabel("全部空闲")
        self.fleet_badge.setObjectName("channelAccountStatusBadge")
        self.fleet_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fleet_badge.setMinimumWidth(112)
        current_row.addWidget(self.fleet_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        current_layout.addLayout(current_row)

        # Legacy control owners stay alive for compatibility, but the visible
        # interaction is the account-card workspace below.
        self.account_combo = QComboBox(self)
        self.account_combo.hide()
        self.switch_button = QPushButton("切换到此店铺", self)
        self.switch_button.setObjectName("modalPrimaryButton")
        self.switch_button.clicked.connect(self._switch_selected)
        self.switch_button.hide()

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
        overview_title = QLabel("Makro 店铺工作台")
        overview_title.setObjectName("modalFieldLabel")
        overview_layout.addWidget(overview_title)

        overview_hint = QLabel(
            "每个店铺都是独立任务域。正在后台运行的店铺会继续执行，"
            "切换只改变当前查看和新任务的目标店铺。"
        )
        overview_hint.setObjectName("cardDetailText")
        overview_hint.setWordWrap(True)
        overview_layout.addWidget(overview_hint)

        self.accounts_host = QWidget(overview_card)
        self.accounts_layout = QVBoxLayout(self.accounts_host)
        self.accounts_layout.setContentsMargins(0, 2, 0, 0)
        self.accounts_layout.setSpacing(8)
        overview_layout.addWidget(self.accounts_host)

        # Hidden compatibility summary for older contracts / diagnostics.
        self.account_overview = QLabel()
        self.account_overview.hide()
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
            lane_state.connect(self._on_lane_state_changed)
        self.reload()

    def _account_port(self, account: Any) -> int:
        getter = getattr(self.manager, "channel_browser_port", None)
        return int(getter(account)) if callable(getter) else 0

    @staticmethod
    def _status_color(status: str) -> str:
        name = str(status or "IDLE").upper()
        if name == "RUNNING":
            return "#9fe2bd"
        if name in {"READY", "PREPARED", "COMPLETE"}:
            return "#b9d9f2"
        if name in {"FAILED", "ERROR", "POISONED"}:
            return "#f18da0"
        if name in {"REVIEW", "STOPPED"}:
            return "#f4cb7a"
        return "#aeb9c7"

    def _account_snapshot(self, account: Any) -> dict[str, Any]:
        runtime = getattr(self.manager.window, "_batch_parallel_runtime", None)
        getter = getattr(runtime, "account_slot_snapshot", None)
        if not callable(getter):
            return {
                "has_batch": False,
                "status": "IDLE",
                "running": False,
                "summary": {},
            }
        try:
            return dict(getter(str(account.account_id)))
        except Exception as exc:
            return {
                "has_batch": False,
                "status": "ERROR",
                "running": False,
                "summary": {},
                "error": str(exc),
            }

    def _clear_account_cards(self) -> None:
        while self.accounts_layout.count():
            item = self.accounts_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._account_cards.clear()

    def _ensure_account_cards(self, accounts: tuple[Any, ...]) -> None:
        wanted = tuple(str(account.account_id) for account in accounts)
        if tuple(self._account_cards) == wanted:
            return
        self._clear_account_cards()

        for account in accounts:
            account_id = str(account.account_id)
            frame = QFrame(self.accounts_host)
            frame.setObjectName("channelAccountCard")
            box = QVBoxLayout(frame)
            box.setContentsMargins(13, 11, 13, 11)
            box.setSpacing(7)

            header = QHBoxLayout()
            identity = QVBoxLayout()
            identity.setSpacing(1)
            name = QLabel(str(account.label))
            name.setObjectName("modalFieldLabel")
            meta = QLabel()
            meta.setObjectName("modalMetaLabel")
            identity.addWidget(name)
            identity.addWidget(meta)
            header.addLayout(identity, 1)

            badge = QLabel("● IDLE")
            badge.setObjectName("channelAccountStatusBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(92)
            header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            box.addLayout(header)

            summary = QLabel()
            summary.setObjectName("cardDetailText")
            summary.setWordWrap(True)
            box.addWidget(summary)

            footer = QHBoxLayout()
            lane = QLabel()
            lane.setObjectName("modalMetaLabel")
            footer.addWidget(lane, 1)
            action = QPushButton("切换查看")
            action.setObjectName("quietButton")
            action.clicked.connect(
                lambda _checked=False, target=account_id: self._switch_account(target)
            )
            footer.addWidget(action)
            box.addLayout(footer)

            self.accounts_layout.addWidget(frame)
            self._account_cards[account_id] = {
                "frame": frame,
                "name": name,
                "meta": meta,
                "badge": badge,
                "summary": summary,
                "lane": lane,
                "action": action,
            }

    def _refresh_account_cards(self) -> None:
        accounts = tuple(self.manager.list_channel_accounts())
        if not accounts:
            self._clear_account_cards()
            self.account_overview.setText("暂无 Makro 账号")
            self.current_store_name.setText("等待 Makro 店铺")
            self.current_store_meta.setText("连接一个店铺后即可创建独立任务域")
            self.fleet_badge.setText("无账号")
            return

        self._ensure_account_cards(accounts)
        current = self.manager.channel_account
        selected = self.manager.selected_channel_account()
        change_blocked = getattr(self.manager, "channel_account_change_blocked", None)
        blocked = bool(change_blocked()) if callable(change_blocked) else bool(self.manager.is_busy())

        running_count = 0
        compatibility_lines: list[str] = []
        for account in accounts:
            account_id = str(account.account_id)
            widgets = self._account_cards[account_id]
            snapshot = self._account_snapshot(account)
            summary = snapshot.get("summary") or {}
            running = bool(snapshot.get("running"))
            running_count += int(running)
            has_batch = bool(snapshot.get("has_batch"))
            status = "RUNNING" if running else (
                str(snapshot.get("status") or "IDLE").upper() if has_batch else "IDLE"
            )
            port = self._account_port(account)
            is_current = account_id == str(current.account_id)
            is_selected = account_id == str(selected.account_id)

            role = "当前查看" if is_current else "准备切换" if is_selected else "独立任务域"
            if running and not is_current:
                role = "后台运行"
            widgets["name"].setText(str(account.label))
            widgets["meta"].setText(f"{role} · CDP {port}")
            widgets["badge"].setText(f"● {status}")
            widgets["badge"].setStyleSheet(
                f"color: {self._status_color(status)}; font-weight: 760;"
            )

            if has_batch:
                widgets["summary"].setText(
                    f"{int(summary.get('total') or 0)} TASKS   ·   "
                    f"{int(summary.get('processing') or 0)} PROCESSING   ·   "
                    f"{int(summary.get('ready') or 0)} READY   ·   "
                    f"{int(summary.get('done') or 0)} DONE"
                )
            else:
                widgets["summary"].setText("暂无 Batch · 可在这个店铺独立创建任务")

            failed = int(summary.get("failed") or 0)
            review = int(summary.get("review") or 0)
            tail: list[str] = []
            if failed:
                tail.append(f"{failed} FAILED")
            if review:
                tail.append(f"{review} REVIEW")
            widgets["lane"].setText(
                "独立 Profile / Browser owner"
                + ((" · " + " · ".join(tail)) if tail else "")
            )

            action = widgets["action"]
            if is_current:
                action.setText("当前店铺")
                action.setEnabled(False)
            elif is_selected:
                action.setText("切换中…")
                action.setEnabled(False)
            else:
                action.setText("切换查看")
                action.setEnabled(not blocked)

            compatibility_lines.append(
                f"{'●' if is_current else '○'} {account.label} · CDP {port} · {status}"
            )

        self.account_overview.setText("\n".join(compatibility_lines))
        current_port = self._account_port(current)
        self.current_store_name.setText(str(current.label))
        self.current_store_meta.setText(
            f"当前控制面 · CDP {current_port} · 独立 Profile / scheduler lane"
        )
        if running_count:
            self.fleet_badge.setText(f"{running_count} 店运行中")
            self.fleet_badge.setStyleSheet("color: #9fe2bd; font-weight: 760;")
        else:
            self.fleet_badge.setText("全部空闲")
            self.fleet_badge.setStyleSheet("color: #aeb9c7; font-weight: 720;")

    def _on_lane_state_changed(self, _account_id: str, _snapshot: object) -> None:
        self._refresh_account_cards()

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

        self._refresh_account_cards()

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
        self._refresh_account_cards()

    def _set_action(self, text: str, *, error: bool = False) -> None:
        self.action_status.setText(text)
        self.action_status.setStyleSheet(
            "color: #f18da0; font-weight: 650;"
            if error
            else "color: #b9d9f2; font-weight: 650;"
        )

    def _switch_account(self, account_id: str) -> None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return
        try:
            account = self.manager.request_activate_channel_account(account_id)
        except Exception as exc:
            self._set_action(str(exc), error=True)
            self.reload()
            return
        self._set_action(
            f"正在切换到 {account.label}。该店铺自己的 Browser / Batch lane 会成为当前控制面；"
            "其他正在运行的店铺继续在后台执行。"
        )
        self.reload()

    def _switch_selected(self) -> None:
        self._switch_account(str(self.account_combo.currentData() or ""))

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
        self.button = QPushButton("Makro")
        self.button.setObjectName("quietButton")
        self.button.setToolTip("Makro 多店铺工作台 · 独立任务 / Browser lane / 后台状态")
        self.button.clicked.connect(self.open)
        self._panel: ChannelAccountCenterPanel | None = None
        self._install_header_button()
        self._refresh_button_label()
        self.manager.status_changed.connect(self._refresh_button_label)

    def _refresh_button_label(self, *_args: object) -> None:
        account = getattr(self.manager, "channel_account", None)
        label = str(getattr(account, "label", "") or "店铺").strip()
        compact = label if len(label) <= 14 else label[:13] + "…"
        self.button.setText(f"Makro · {compact}")
        self.button.setToolTip(
            f"当前店铺：{label}\n打开多店铺工作台，查看并切换独立任务 / Browser lane。"
        )

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
