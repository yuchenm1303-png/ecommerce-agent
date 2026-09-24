from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


_STATE_VERSION = 1


class BatchAccountSlotStore:
    """Persist only the latest Batch root owned by each Makro account.

    The Batch itself remains the canonical ``batch.json`` under ``logs/batch-runs``.
    This index contains no marketplace credentials and stores runtime-root-relative
    paths only. Loading rejects paths outside the managed Batch root so corrupted
    metadata cannot make account switching read arbitrary files.
    """

    def __init__(self, runtime_root: str | Path, *, scope_token: str) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.scope_token = str(scope_token or "").strip()
        if not self.scope_token:
            raise ValueError("batch account slot scope_token must not be empty")
        self.batch_root = (self.runtime_root / "logs" / "batch-runs").resolve()
        self.state_path = (
            self.runtime_root
            / "channel_accounts"
            / "batch_slots"
            / f"{self.scope_token}.json"
        )
        self._state = self._load()

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        value = str(account_id or "").strip()
        if not value:
            raise ValueError("batch account slot account_id must not be empty")
        return value

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": _STATE_VERSION, "slots": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Makro Batch 槽位索引无法读取，已停止恢复以避免串号：{self.state_path}"
            ) from exc
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != _STATE_VERSION:
            raise RuntimeError("Makro Batch 槽位索引版本无效，已停止恢复。")
        slots = payload.get("slots")
        if not isinstance(slots, dict):
            raise RuntimeError("Makro Batch 槽位索引结构无效，已停止恢复。")
        return payload

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp, self.state_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def remember(self, account_id: str, batch_root: str | Path) -> None:
        account = self._normalize_account_id(account_id)
        root = Path(batch_root).resolve()
        if not root.is_relative_to(self.batch_root):
            raise ValueError("Batch slot root must stay under logs/batch-runs")
        relative = root.relative_to(self.runtime_root).as_posix()
        self._state["slots"][account] = relative
        self._save()

    def forget(self, account_id: str) -> None:
        account = self._normalize_account_id(account_id)
        if self._state["slots"].pop(account, None) is not None:
            self._save()

    def batch_path(self, account_id: str) -> Path | None:
        account = self._normalize_account_id(account_id)
        raw = str(self._state["slots"].get(account) or "").strip()
        if not raw:
            return None
        candidate = (self.runtime_root / raw).resolve()
        if not candidate.is_relative_to(self.batch_root):
            raise RuntimeError("Makro Batch 槽位路径越界，已拒绝恢复。")
        batch_json = candidate / "batch.json"
        return batch_json if batch_json.is_file() else None


__all__ = ["BatchAccountSlotStore"]
