from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app.browser_session import (
    CdpSessionLease,
    acquire_cdp_session_lease,
    cdp_endpoint,
    is_cdp_ready,
)
from .batch_model import BatchJob, BatchRun


@dataclass(slots=True, frozen=True)
class SharedBatchBrowser:
    """The one long-lived Makro Edge instance shared by all Batch job tabs."""

    cdp_port: int
    profile_dir: Path


def shared_batch_browser(
    project_root: str | Path,
    *,
    cdp_port: int,
    profile_dir: str | Path | None = None,
) -> SharedBatchBrowser:
    """Describe the already-managed Makro browser used by one Batch.

    ``profile_dir`` is explicit for account-bound sessions. The legacy default is
    retained only for older callers/tests that predate multi-account Makro
    profiles. New GUI Batch work always passes the selected account profile.
    """

    root = Path(project_root).resolve()
    resolved_profile = (
        Path(profile_dir).resolve()
        if profile_dir is not None
        else (root / "browser_profiles" / "makro-edge").resolve()
    )
    return SharedBatchBrowser(
        cdp_port=int(cdp_port),
        profile_dir=resolved_profile,
    )


def bind_job_shared_browser(job: BatchJob, browser: SharedBatchBrowser) -> None:
    """Persist that one Job belongs to the shared Edge, while targetId owns its tab."""

    job.browser_lane = 0
    job.makro_cdp_port = int(browser.cdp_port)
    job.makro_profile_dir = str(browser.profile_dir.resolve())
    job.touch()


def bind_batch_shared_browser(batch: BatchRun, browser: SharedBatchBrowser) -> None:
    for job in batch.jobs:
        bind_job_shared_browser(job, browser)


def browser_instance_token(port: int) -> str:
    """Return an identity token that changes when the external Edge process restarts."""

    try:
        with urllib.request.urlopen(
            f"{cdp_endpoint(int(port))}/json/version",
            timeout=0.6,
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return str(payload.get("webSocketDebuggerUrl") or "").strip()
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return ""


@dataclass(slots=True)
class BatchSharedBrowserOwner:
    """One GUI-owned CDP lease inherited by all concurrent Batch child workers.

    The parent GUI holds the only external session lease for the whole prepared
    Batch. QProcess children inherit its cryptographic environment token, so each
    EdgeHarness may re-enter the same owner without waiting on another job. This
    keeps one Edge process while targetId remains the per-product tab ownership
    boundary. Unrelated processes still cannot acquire the same browser session.
    """

    browser: SharedBatchBrowser
    _lease: CdpSessionLease | None = field(default=None, init=False, repr=False)
    _instance_token: str = field(default="", init=False, repr=False)

    @property
    def acquired(self) -> bool:
        return self._lease is not None

    @property
    def instance_token(self) -> str:
        return self._instance_token

    def acquire(self) -> None:
        if self._lease is not None:
            self.assert_alive()
            return
        port = int(self.browser.cdp_port)
        if not is_cdp_ready(port, timeout_s=1.0):
            raise RuntimeError(
                f"主 Makro Browser CDP {port} 未就绪，不能建立单浏览器 Batch owner。"
            )
        before = browser_instance_token(port)
        if not before:
            raise RuntimeError(f"Makro Browser CDP {port} 没有可验证 browser instance token。")

        lease = acquire_cdp_session_lease(port)
        after = browser_instance_token(port)
        if not after or after != before:
            lease.release()
            raise RuntimeError(
                "Makro Browser 在 Batch owner 建立期间发生重启；拒绝继承不确定的页面所有权。"
            )
        self._lease = lease
        self._instance_token = after

    def assert_alive(self) -> None:
        if self._lease is None:
            raise RuntimeError("Batch 尚未持有 Makro Browser session owner。")
        port = int(self.browser.cdp_port)
        current = browser_instance_token(port)
        if not current:
            raise RuntimeError(
                f"Batch 准备后的 Makro Browser CDP {port} 已关闭；请重新批量准备。"
            )
        if current != self._instance_token:
            raise RuntimeError(
                "Batch 准备后的 Makro Browser 已被重启，旧 targetId 全部失效；请重新批量准备。"
            )

    def release(self) -> None:
        lease = self._lease
        self._lease = None
        self._instance_token = ""
        if lease is not None:
            lease.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


__all__ = [
    "BatchSharedBrowserOwner",
    "SharedBatchBrowser",
    "bind_batch_shared_browser",
    "bind_job_shared_browser",
    "browser_instance_token",
    "shared_batch_browser",
]
