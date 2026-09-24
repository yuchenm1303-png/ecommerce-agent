from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any
from uuid import uuid4

from app.browser_visual_hud import browser_visual_hud_advice
from app.business_decisions import (
    is_user_decision_business_field,
    price_decision_advisory,
    user_decision_business_key,
)
from app.fill_plan import BLOCKED, LiveFillPlan
from app.makro import base_section_title
from app.makro.domain import MakroDomainAdapter
from app.makro.field_engine import control_locator, read_control
from app.makro.runtime_contract import RuntimeEvent, RuntimeState
from app.required_overrides import required_override_binding
from makro_preview_listing import _item_identity, _open_and_index_section


_USER_EVENT_STATE = "__listingStudioUserDecisionEvents"
USER_INPUT_STABLE_MS = 900


def _value_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if control.get("field_kind") != "option"
        and not str(control.get("name") or "").endswith("_qualifier")
    ]


def _answer_values(answer: Any) -> list[str]:
    values = [
        str(value).strip()
        for value in list(getattr(answer, "answer_values", []) or [])
        if str(value).strip()
    ]
    if values:
        return values
    scalar = str(getattr(answer, "answer", "") or "").strip()
    return [scalar] if scalar else []


def _field_payload(item: Any) -> dict[str, str]:
    return {
        "attribute_key": str(getattr(item, "attribute_key", "") or ""),
        "label": str(getattr(item, "label", "") or ""),
    }


def pending_user_decision_items(
    plan: LiveFillPlan,
    *,
    section: str | None = None,
) -> list[Any]:
    wanted = base_section_title(section or "").casefold() if section else ""
    output: list[Any] = []
    for item in plan.items:
        if not bool(getattr(item, "required", False)):
            continue
        if str(getattr(item, "action", "") or "").casefold() != BLOCKED:
            continue
        if not is_user_decision_business_field(_field_payload(item)):
            continue
        if wanted and base_section_title(
            str(getattr(item, "section_heading", "") or "")
        ).casefold() != wanted:
            continue
        output.append(item)
    return output


def _confirmed_price_values(plan: LiveFillPlan) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in plan.items:
        key = user_decision_business_key(_field_payload(item))
        if not key:
            continue
        resolution = getattr(item, "resolution", None)
        if str(getattr(resolution, "source_type", "") or "").casefold() != "user":
            continue
        values = _answer_values(resolution)
        if values:
            output[key] = values[0]
    return output


def _emit_waiting(item: Any, detail: str) -> None:
    event = RuntimeEvent(
        state=RuntimeState.WAITING_FOR_USER,
        title=f"等待你确认 · {getattr(item, 'label', '经营字段')}",
        detail=detail,
        phase="Real Execution · Seller Decision",
        progress=45,
        requires_user=True,
        advisor="seller-decision",
        target_id=str(getattr(item, "attribute_key", "") or ""),
    )
    print("RUNTIME_EVENT " + json.dumps(event.as_dict(), ensure_ascii=False), flush=True)


def _emit_confirmed(item: Any) -> None:
    event = RuntimeEvent(
        state=RuntimeState.RUNNING,
        title=f"已确认 · {getattr(item, 'label', '经营字段')}",
        detail="已读取你在 Makro live 字段中的真实输入；仅把该值作为本次 listing 的用户决策继续执行。",
        phase="Real Execution · Seller Decision",
        progress=45,
        advisor="seller-decision",
        target_id=str(getattr(item, "attribute_key", "") or ""),
    )
    print("RUNTIME_EVENT " + json.dumps(event.as_dict(), ensure_ascii=False), flush=True)


def _arm_trusted_input(locator: Any, token: str) -> None:
    locator.evaluate(
        """(el, payload) => {
          const key = payload.key;
          const token = payload.token;
          const root = window[key] || (window[key] = Object.create(null));
          const state = {
            touched:false, value:'', at:0, eventType:'', mark:null, target:el
          };
          const mark = event => {
            if (!event.isTrusted) return;
            const value = ('value' in el) ? String(el.value || '') : String(el.textContent || '');
            state.touched=true;
            state.value=value.trim();
            state.at=Date.now();
            state.eventType=String(event.type || '');
          };
          state.mark=mark;
          root[token]=state;
          el.addEventListener('input', mark, true);
          el.addEventListener('change', mark, true);
          el.addEventListener('blur', mark, true);
        }""",
        {"key": _USER_EVENT_STATE, "token": token},
    )


def _trusted_event(page: Any, token: str) -> dict[str, Any]:
    value = page.evaluate(
        """payload => {
          const root = window[payload.key] || {};
          const state = root[payload.token];
          return state
            ? {
                touched:Boolean(state.touched),
                value:String(state.value || ''),
                at:Number(state.at)||0,
                event_type:String(state.eventType || ''),
                stable_for_ms:Math.max(0,Date.now()-(Number(state.at)||Date.now()))
              }
            : {touched:false, value:'', at:0, event_type:'', stable_for_ms:0};
        }""",
        {"key": _USER_EVENT_STATE, "token": token},
    )
    return value if isinstance(value, dict) else {"touched": False, "value": "", "at": 0}


def _clear_trusted_event(page: Any, token: str) -> None:
    try:
        page.evaluate(
            """payload => {
              const root = window[payload.key];
              if (!root) return;
              const state = root[payload.token];
              if (state && typeof state.mark === 'function') {
                const target = state.target;
                if (target) {
                  try { target.removeEventListener('input', state.mark, true); } catch (_) {}
                  try { target.removeEventListener('change', state.mark, true); } catch (_) {}
                  try { target.removeEventListener('blur', state.mark, true); } catch (_) {}
                }
              }
              delete root[payload.token];
            }""",
            {"key": _USER_EVENT_STATE, "token": token},
        )
    except Exception:
        pass


def _wait_for_user_value(
    adapter: MakroDomainAdapter,
    field: dict[str, Any],
    section_path: str,
    *,
    timeout_ms: int,
    poll_ms: int,
) -> str:
    controls = _value_controls(field)
    if len(controls) != 1:
        raise RuntimeError(
            f"{field.get('label') or field.get('attribute_key')} 当前经营决策字段不是单一 value control；"
            "为了避免误读，运行时人工确认已 fail closed。"
        )
    control = controls[0]
    locator, _selector = control_locator(adapter.page, control, section_path)
    token = uuid4().hex
    _arm_trusted_input(locator, token)
    deadline = time.monotonic() + max(1, int(timeout_ms)) / 1000

    try:
        while time.monotonic() < deadline:
            state = _trusted_event(adapter.page, token)
            if bool(state.get("touched")):
                event_type = str(state.get("event_type") or "")
                stable_for_ms = int(state.get("stable_for_ms") or 0)
                confirmed = event_type in {"change", "blur"} or stable_for_ms >= USER_INPUT_STABLE_MS
                if confirmed:
                    current = read_control(
                        adapter.page,
                        control,
                        section_path=section_path,
                        timeout_ms=3_000,
                    ).strip()
                    if current:
                        return current
            adapter.page.wait_for_timeout(max(50, int(poll_ms)))
    finally:
        _clear_trusted_event(adapter.page, token)

    raise RuntimeError(
        f"等待 {field.get('label') or field.get('attribute_key')} 用户输入超时；"
        "未保存 section，也没有用占位值继续。"
    )


def collect_runtime_user_decisions(
    adapter: MakroDomainAdapter,
    plan: LiveFillPlan,
    *,
    section: str | None = None,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    timeout_ms: int = 900_000,
    poll_ms: int = 180,
) -> list[dict[str, Any]]:
    """Collect unresolved seller decisions from trusted human input in Makro.

    The function opens the relevant live section only for decision capture,
    displays the existing non-interactive HUD beside the exact field, waits for a
    trusted browser input/change event, reads the live value, and then cancels
    the temporary section transaction. Nothing is saved here. The returned
    overrides go through the normal hard validators and price-relation gate.
    """

    pending = pending_user_decision_items(plan, section=section)
    if not pending:
        return []

    by_section: dict[str, list[Any]] = defaultdict(list)
    for item in pending:
        by_section[base_section_title(str(getattr(item, "section_heading", "") or ""))].append(item)

    confirmed = _confirmed_price_values(plan)
    overrides: list[dict[str, Any]] = []

    for section_title, items in by_section.items():
        if not section_title:
            raise RuntimeError("经营决策字段缺少 section identity，无法安全进入 live HUD 确认。")
        try:
            for item in items:
                section_path, live = _open_and_index_section(
                    adapter,
                    section_title,
                    wait_ms=wait_ms,
                    max_scroll_steps=max_scroll_steps,
                )
                matches = live.get(_item_identity(item), [])
                if len(matches) != 1:
                    raise RuntimeError(
                        f"{item.label} 无法唯一绑定当前 Makro live field；matches={len(matches)}。"
                    )
                field = matches[0]
                controls = _value_controls(field)
                if len(controls) != 1:
                    raise RuntimeError(f"{item.label} live value control 数量={len(controls)}，拒绝猜测。")
                locator, _selector = control_locator(adapter.page, controls[0], section_path)

                key = user_decision_business_key(field)
                counterpart_key = "flipkart_selling_price" if key == "mrp" else "mrp"
                advisory = price_decision_advisory(
                    field,
                    counterpart_value=confirmed.get(counterpart_key, ""),
                )
                advisory["hold_ms"] = int(timeout_ms)
                browser_visual_hud_advice(locator, advisory, phase=2)
                _emit_waiting(
                    item,
                    "请直接在当前 Makro 字段中输入你决定的价格。HUD 只提供参考和约束，"
                    "不会替你选择或自动使用临时占位价。",
                )

                while True:
                    value = _wait_for_user_value(
                        adapter,
                        field,
                        section_path,
                        timeout_ms=timeout_ms,
                        poll_ms=poll_ms,
                    )
                    candidate = price_decision_advisory(
                        field,
                        confirmed_value=value,
                        counterpart_value=confirmed.get(counterpart_key, ""),
                    )
                    warning = str(candidate.get("warning") or "").strip()
                    candidate["hold_ms"] = int(timeout_ms if warning else 2_500)
                    browser_visual_hud_advice(locator, candidate, phase=4 if not warning else 2)
                    if not warning:
                        break
                    _emit_waiting(item, warning + " 请修改当前字段后再次确认。")

                confirmed[key] = value
                overrides.append(
                    {
                        **required_override_binding(field),
                        "values": [value],
                        "source_type": "user",
                        "reason": (
                            "Explicit seller decision captured from a trusted live Makro "
                            "input/change event while the runtime HUD was waiting."
                        ),
                    }
                )
                _emit_confirmed(item)
                adapter.page.wait_for_timeout(450)
        finally:
            # Decision capture is never a persistence path. Discard the temporary
            # live edits; the canonical executor will later write the confirmed
            # override and perform normal readback + Save/reopen verification.
            try:
                adapter.cancel_section(section_title)
            except Exception as exc:
                raise RuntimeError(
                    f"{section_title} 人工决策采集后无法安全 Cancel 临时事务：{exc}"
                ) from exc

    return overrides


__all__ = [
    "USER_INPUT_STABLE_MS",
    "collect_runtime_user_decisions",
    "pending_user_decision_items",
]
