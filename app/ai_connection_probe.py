from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from PIL import Image, ImageDraw

from .providers.dashscope_web_search import negotiate_responses_web_search


_VERIFICATION_FILENAME = "ai-capability-verification.json"
_ROLE_REQUIREMENTS = {
    "semantic": ("chat_completions", "strict_json_schema", "vision"),
    "fact": ("chat_completions", "strict_json_schema"),
    "web": ("responses_api", "web_search", "web_sources"),
}
_COLOR_PALETTE = {
    "red": (232, 48, 48),
    "green": (45, 176, 83),
    "blue": (53, 102, 232),
    "yellow": (241, 202, 54),
    "magenta": (202, 64, 190),
    "cyan": (46, 189, 199),
}


class CapabilityProbeError(RuntimeError):
    """Raised when an AI connection/model cannot satisfy a required role."""


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: str
    base_url: str
    model: str
    api_key: str

    def normalized(self) -> "RoleBinding":
        role = str(self.role or "").strip().casefold()
        if role not in _ROLE_REQUIREMENTS:
            raise CapabilityProbeError(f"unsupported AI role: {self.role!r}")
        base_url = str(self.base_url or "").strip().rstrip("/")
        model = str(self.model or "").strip()
        api_key = str(self.api_key or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CapabilityProbeError(f"{role} Base URL 必须是完整的 http(s) 地址。")
        if not model:
            raise CapabilityProbeError(f"{role} 模型不能为空。")
        if not api_key:
            raise CapabilityProbeError(f"{role} API Key 不能为空。")
        return RoleBinding(role=role, base_url=base_url, model=model, api_key=api_key)


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RoleCapabilityReport:
    role: str
    base_url: str
    model: str
    passed: bool
    checks: tuple[CapabilityCheck, ...] = ()
    error: str = ""
    failed_stage: str = ""
    log_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "base_url": self.base_url,
            "model": self.model,
            "passed": self.passed,
            "checks": [asdict(item) for item in self.checks],
            "error": self.error,
            "failed_stage": self.failed_stage,
            "log_path": self.log_path,
        }


@dataclass(frozen=True, slots=True)
class ModelCatalogResult:
    base_url: str
    models: tuple[str, ...] = ()
    catalog_available: bool = True
    error: str = ""
    log_path: str = ""
    elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    role: str
    base_url: str
    model: str
    key_fingerprint: str
    binding_signature: str
    passed: bool
    checks: tuple[CapabilityCheck, ...] = ()
    verified_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "base_url": self.base_url,
            "model": self.model,
            "key_fingerprint": self.key_fingerprint,
            "binding_signature": self.binding_signature,
            "passed": self.passed,
            "checks": [asdict(item) for item in self.checks],
            "verified_at": self.verified_at,
        }


@dataclass(frozen=True, slots=True)
class VerificationSnapshot:
    version: int = 1
    records: tuple[VerificationRecord, ...] = ()

    def by_role(self) -> dict[str, VerificationRecord]:
        return {record.role: record for record in self.records}

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "records": [record.as_dict() for record in self.records]}


def _client(binding: RoleBinding, *, timeout: float, client_factory: Callable[..., Any] | None) -> Any:
    normalized = binding.normalized()
    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise CapabilityProbeError("缺少 openai Python SDK。") from exc
        client_factory = OpenAI
    return client_factory(
        api_key=normalized.api_key,
        base_url=normalized.base_url,
        timeout=float(timeout),
        max_retries=0,
    )


def _sanitize_error(exc: BaseException, *, secrets: Iterable[str]) -> str:
    text = str(exc or exc.__class__.__name__).strip()
    for secret in secrets:
        value = str(secret or "").strip()
        if value:
            text = text.replace(value, "***")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer ***", text)
    return text[:1200] or exc.__class__.__name__


def _message_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise CapabilityProbeError("Chat Completions 没有返回 choices。")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _parse_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    candidates = [raw]
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]).strip())
    first, last = raw.find("{"), raw.rfind("}")
    if 0 <= first < last:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    raise CapabilityProbeError("模型没有返回可解析的 JSON object。")


def discover_models(
    *,
    base_url: str,
    api_key: str,
    timeout: float = 20.0,
    client_factory: Callable[..., Any] | None = None,
    diagnostic_dir: Path | None = None,
) -> ModelCatalogResult:
    from dataclasses import replace
    import time
    from .ai_probe_diagnostics import ProbeDiagnostics
    binding = RoleBinding("fact", base_url, "_catalog_probe_", api_key).normalized()
    diagnostics = ProbeDiagnostics(binding, timeout, diagnostic_dir)
    diagnostics.stage = "model_catalog"
    result = _discover_models(binding, timeout=timeout, client_factory=client_factory, diagnostics=diagnostics)
    elapsed = round(time.monotonic() - diagnostics.started, 3)
    diagnostics.write("catalog_finished", catalog_available=result.catalog_available,
                      model_count=len(result.models), elapsed_seconds=elapsed, error=diagnostics.clean(result.error))
    return replace(result, log_path=str(diagnostics.path) if diagnostics.path else "", elapsed_seconds=elapsed)


def _discover_models(binding, *, timeout, client_factory, diagnostics):
    api_key = binding.api_key
    try:
        client = _client(binding, timeout=timeout, client_factory=client_factory)
        response = diagnostics.call("/models", client.models.list)
        data = list(getattr(response, "data", response) or [])
        models = sorted(
            {
                str(getattr(item, "id", "") or (item.get("id") if isinstance(item, dict) else "")).strip()
                for item in data
            }
            - {""},
            key=str.casefold,
        )
        if not models:
            return ModelCatalogResult(
                base_url=binding.base_url,
                models=(),
                catalog_available=True,
                error="模型目录请求成功，但没有返回任何模型 ID。",
            )
        return ModelCatalogResult(base_url=binding.base_url, models=tuple(models))
    except Exception as exc:
        return ModelCatalogResult(
            base_url=binding.base_url,
            models=(),
            catalog_available=False,
            error=_sanitize_error(exc, secrets=(api_key,)),
        )


def _structured_probe(client: Any, **request: Any) -> dict[str, Any]:
    """Allow one format correction without relaxing schema or image validation."""
    for attempt in range(2):
        response = client.chat.completions.create(**request)
        text = _message_text(response)
        try:
            return _parse_json(text)
        except CapabilityProbeError as exc:
            if attempt:
                choices = getattr(response, "choices", ()) or ()
                finish = getattr(choices[0], "finish_reason", "unknown") if choices else "missing_choices"
                raise CapabilityProbeError(f"连续两次未返回有效 JSON；文本长度={len(text)}，结束原因={finish}。") from exc
            notify = getattr(client, "validation_retry", None)
            if callable(notify):
                notify()
            request = dict(request)
            request["messages"] = [*request["messages"], {"role": "user", "content": "The response was not valid JSON. Return only one JSON object matching the supplied schema. No prose or markdown. Inspect the supplied image if present; do not guess."}]
    raise AssertionError("unreachable")


def _strict_json_probe(client: Any, *, model: str, timeout: float) -> None:
    schema = {
        "type": "object",
        "properties": {"probe": {"type": "string", "enum": ["ok"]}},
        "required": ["probe"],
        "additionalProperties": False,
    }
    payload = _structured_probe(client,
        model=model,
        messages=[
            {"role": "system", "content": "Return only the requested structured result."},
            {"role": "user", "content": 'Set the field "probe" to exactly "ok".'},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ecommerce_capability_probe", "strict": True, "schema": schema},
        },
        timeout=timeout,
        extra_body={"enable_thinking": False},
    )
    if payload != {"probe": "ok"}:
        raise CapabilityProbeError(f"Strict JSON Schema 返回内容不符合约定：{payload!r}")


def _vision_image(sequence: tuple[str, str, str]) -> str:
    width, height = 540, 180
    image = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    segment = width // 3
    for index, name in enumerate(sequence):
        draw.rectangle(
            (index * segment, 0, (index + 1) * segment - 1, height - 1),
            fill=_COLOR_PALETTE[name],
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _vision_probe(
    client: Any,
    *,
    model: str,
    timeout: float,
    sequence: tuple[str, str, str] | None = None,
) -> None:
    if sequence is None:
        sequence = tuple(random.SystemRandom().sample(list(_COLOR_PALETTE), 3))  # type: ignore[assignment]
    allowed = list(_COLOR_PALETTE)
    schema = {
        "type": "object",
        "properties": {
            "colors": {
                "type": "array",
                "items": {"type": "string", "enum": allowed},
                "minItems": 3,
                "maxItems": 3,
            }
        },
        "required": ["colors"],
        "additionalProperties": False,
    }
    payload = _structured_probe(client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": "Inspect the supplied image and return only the requested structured result.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "The image contains three solid vertical color blocks. "
                            "Return their colors from left to right using only one of: "
                            + ", ".join(allowed)
                            + '. Return only a JSON object with the field "colors" containing exactly three color names. No other text.'
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": _vision_image(sequence)}},
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ecommerce_vision_probe", "strict": True, "schema": schema},
        },
        timeout=timeout,
        extra_body={"enable_thinking": False},
    )
    actual = tuple(str(item).casefold() for item in payload.get("colors") or [])
    expected = tuple(item.casefold() for item in sequence)
    if actual != expected:
        raise CapabilityProbeError(
            "Vision 请求虽然返回了结果，但没有正确读取测试图片；"
            f"expected={expected!r}, actual={actual!r}"
        )


def _web_probe(client: Any, *, model: str, base_url: str) -> str:
    prompt = (
        "You MUST use the web-search tool for this capability test. "
        "Search the web for the official OpenAI homepage and report its domain. "
        "The test is valid only when the Responses transport emits a real web_search_call "
        "and exposes at least one HTTP(S) source URL."
    )
    _response, protocol = negotiate_responses_web_search(
        client,
        model=model,
        prompt=prompt,
        base_url=base_url,
    )
    return protocol


def probe_role(
    binding: RoleBinding,
    *,
    timeout: float = 30.0,
    client_factory: Callable[..., Any] | None = None,
    vision_sequence: tuple[str, str, str] | None = None,
    diagnostic_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> RoleCapabilityReport:
    from .ai_probe_diagnostics import ProbeDiagnostics
    normalized = binding.normalized()
    diagnostics = ProbeDiagnostics(normalized, timeout, diagnostic_dir)
    diagnostics.progress_callback = progress_callback
    diagnostics.set_stage("client_init")
    report = _probe_role(normalized, timeout=timeout, client_factory=client_factory,
                         vision_sequence=vision_sequence, diagnostics=diagnostics)
    return diagnostics.finish(report)


def _probe_role(binding, *, timeout, client_factory, vision_sequence, diagnostics):
    normalized = binding.normalized()
    checks: list[CapabilityCheck] = []
    try:
        client = _client(normalized, timeout=timeout, client_factory=client_factory)
        client = diagnostics.wrap(client)
        if normalized.role in {"semantic", "fact"}:
            diagnostics.set_stage("strict_json_schema")
            try:
                _strict_json_probe(client, model=normalized.model, timeout=timeout)
                checks.extend(
                    (
                        CapabilityCheck("chat_completions", True, "Chat Completions 可调用"),
                        CapabilityCheck("strict_json_schema", True, "严格 JSON Schema 可执行并通过结果校验"),
                    )
                )
            except Exception as exc:
                detail = _sanitize_error(exc, secrets=(normalized.api_key,))
                checks.extend(
                    (
                        CapabilityCheck("chat_completions", False, detail),
                        CapabilityCheck("strict_json_schema", False, detail),
                    )
                )
                return RoleCapabilityReport(
                    normalized.role,
                    normalized.base_url,
                    normalized.model,
                    False,
                    tuple(checks),
                    detail,
                )

        if normalized.role == "semantic":
            diagnostics.set_stage("vision")
            try:
                _vision_probe(
                    client,
                    model=normalized.model,
                    timeout=timeout,
                    sequence=vision_sequence,
                )
                checks.append(
                    CapabilityCheck("vision", True, "图片输入被实际读取，测试图颜色顺序验证正确")
                )
            except Exception as exc:
                detail = _sanitize_error(exc, secrets=(normalized.api_key,))
                checks.append(CapabilityCheck("vision", False, detail))
                return RoleCapabilityReport(
                    normalized.role,
                    normalized.base_url,
                    normalized.model,
                    False,
                    tuple(checks),
                    detail,
                )

        if normalized.role == "web":
            diagnostics.set_stage("web_search")
            try:
                protocol = _web_probe(
                    client,
                    model=normalized.model,
                    base_url=normalized.base_url,
                )
                checks.extend(
                    (
                        CapabilityCheck("responses_api", True, "Responses API 可调用"),
                        CapabilityCheck("web_search", True, "产生真实 web_search_call"),
                        CapabilityCheck("web_sources", True, "搜索结果包含来源 URL"),
                        CapabilityCheck(
                            "web_protocol_" + protocol.replace("-", "_"),
                            True,
                            f"自动协商命中协议：{protocol}",
                        ),
                    )
                )
            except Exception as exc:
                detail = _sanitize_error(exc, secrets=(normalized.api_key,))
                checks.extend(
                    (
                        CapabilityCheck("responses_api", False, detail),
                        CapabilityCheck("web_search", False, detail),
                        CapabilityCheck("web_sources", False, detail),
                    )
                )
                return RoleCapabilityReport(
                    normalized.role,
                    normalized.base_url,
                    normalized.model,
                    False,
                    tuple(checks),
                    detail,
                )

        names = {item.name for item in checks if item.passed}
        missing = [name for name in _ROLE_REQUIREMENTS[normalized.role] if name not in names]
        if missing:
            detail = "缺少能力：" + ", ".join(missing)
            return RoleCapabilityReport(
                normalized.role,
                normalized.base_url,
                normalized.model,
                False,
                tuple(checks),
                detail,
            )
        return RoleCapabilityReport(
            normalized.role,
            normalized.base_url,
            normalized.model,
            True,
            tuple(checks),
            "",
        )
    except Exception as exc:
        detail = _sanitize_error(exc, secrets=(normalized.api_key,))
        return RoleCapabilityReport(
            normalized.role,
            normalized.base_url,
            normalized.model,
            False,
            tuple(checks),
            detail,
        )


def key_fingerprint(api_key: str) -> str:
    value = str(api_key or "").strip()
    if not value:
        raise CapabilityProbeError("API Key 不能为空。")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def binding_signature(binding: RoleBinding) -> str:
    normalized = binding.normalized()
    payload = {
        "role": normalized.role,
        "base_url": normalized.base_url,
        "model": normalized.model,
        "key_fingerprint": key_fingerprint(normalized.api_key),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verification_path(config_dir: Path) -> Path:
    return Path(config_dir) / _VERIFICATION_FILENAME


def save_verification_snapshot(
    *,
    config_dir: Path,
    bindings: Iterable[RoleBinding],
    reports: Iterable[RoleCapabilityReport],
) -> VerificationSnapshot:
    binding_by_role = {item.normalized().role: item.normalized() for item in bindings}
    report_by_role = {item.role: item for item in reports}
    required = set(_ROLE_REQUIREMENTS)
    if set(binding_by_role) != required or set(report_by_role) != required:
        raise CapabilityProbeError("保存能力验证前必须完整验证 semantic / fact / web 三个角色。")
    now = datetime.now(timezone.utc).isoformat()
    records: list[VerificationRecord] = []
    for role in ("semantic", "fact", "web"):
        binding = binding_by_role[role]
        report = report_by_role[role]
        if not report.passed:
            raise CapabilityProbeError(f"{role} 能力验证未通过，不能保存为可用配置。")
        records.append(
            VerificationRecord(
                role=role,
                base_url=binding.base_url,
                model=binding.model,
                key_fingerprint=key_fingerprint(binding.api_key),
                binding_signature=binding_signature(binding),
                passed=True,
                checks=report.checks,
                verified_at=now,
            )
        )
    snapshot = VerificationSnapshot(records=tuple(records))
    path = verification_path(Path(config_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return snapshot


def load_verification_snapshot(*, config_dir: Path) -> VerificationSnapshot | None:
    path = verification_path(Path(config_dir))
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("version", 0)) != 1:
            raise ValueError("unsupported version")
        records: list[VerificationRecord] = []
        for raw in payload.get("records") or []:
            checks = tuple(
                CapabilityCheck(
                    name=str(item.get("name") or ""),
                    passed=bool(item.get("passed")),
                    detail=str(item.get("detail") or ""),
                )
                for item in raw.get("checks") or []
                if isinstance(item, dict)
            )
            records.append(
                VerificationRecord(
                    role=str(raw.get("role") or ""),
                    base_url=str(raw.get("base_url") or ""),
                    model=str(raw.get("model") or ""),
                    key_fingerprint=str(raw.get("key_fingerprint") or ""),
                    binding_signature=str(raw.get("binding_signature") or ""),
                    passed=bool(raw.get("passed")),
                    checks=checks,
                    verified_at=str(raw.get("verified_at") or ""),
                )
            )
        return VerificationSnapshot(records=tuple(records))
    except Exception as exc:
        raise CapabilityProbeError(f"AI 能力验证记录损坏：{path}") from exc


def assert_verified_if_managed(
    *,
    config_dir: Path,
    bindings: Iterable[RoleBinding],
) -> bool:
    """Enforce verification only after the configuration enters managed mode.

    A missing snapshot is deliberately treated as a legacy configuration so the
    already-working DashScope setup keeps working without forced migration. Once
    a user validates/saves through the new UI, any URL/model/key change invalidates
    the signature and blocks *before* the listing workflow starts.
    """

    snapshot = load_verification_snapshot(config_dir=Path(config_dir))
    if snapshot is None:
        return False
    records = snapshot.by_role()
    for binding in bindings:
        normalized = binding.normalized()
        record = records.get(normalized.role)
        if record is None or not record.passed:
            raise CapabilityProbeError(
                f"{normalized.role} 没有有效的能力验证记录；请在“AI 服务设置”重新测试。"
            )
        if record.binding_signature != binding_signature(normalized):
            raise CapabilityProbeError(
                f"{normalized.role} 的 Base URL / API Key / 模型已经变化；"
                "请先在“AI 服务设置”重新读取模型并执行能力验证。"
            )
        passed = {item.name for item in record.checks if item.passed}
        missing = [name for name in _ROLE_REQUIREMENTS[normalized.role] if name not in passed]
        if missing:
            raise CapabilityProbeError(
                f"{normalized.role} 的验证记录缺少当前所需能力：{', '.join(missing)}。"
            )
    return True


def clear_verification_snapshot(*, config_dir: Path) -> None:
    try:
        verification_path(Path(config_dir)).unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "CapabilityCheck",
    "CapabilityProbeError",
    "ModelCatalogResult",
    "RoleBinding",
    "RoleCapabilityReport",
    "VerificationRecord",
    "VerificationSnapshot",
    "assert_verified_if_managed",
    "binding_signature",
    "clear_verification_snapshot",
    "discover_models",
    "key_fingerprint",
    "load_verification_snapshot",
    "probe_role",
    "save_verification_snapshot",
    "verification_path",
]
