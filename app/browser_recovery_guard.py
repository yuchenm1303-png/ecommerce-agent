from __future__ import annotations

import threading


class BrowserRecoveryGuard:
    """Bound automatic browser relaunches to one attempt per unhealthy episode.

    A temporary CDP miss must not immediately spawn another Edge command because
    Chromium may still be alive with the managed profile. Reissuing the command
    with a start URL can then create another Makro tab instead of restoring CDP.

    The guard therefore requires a small number of consecutive offline endpoint
    observations before the first launch and keeps that launch latched until the
    browser is proven automation-healthy. Merely seeing the endpoint again does
    not re-arm launches; Playwright readiness is the success signal.
    """

    def __init__(self, *, offline_threshold: int = 2) -> None:
        self.offline_threshold = max(1, int(offline_threshold))
        self._lock = threading.Lock()
        self._offline_observations = 0
        self._launch_attempted = False
        self._endpoint_seen_after_attempt = False

    @property
    def offline_observations(self) -> int:
        with self._lock:
            return self._offline_observations

    @property
    def offline_confirmed(self) -> bool:
        with self._lock:
            return self._offline_observations >= self.offline_threshold

    @property
    def launch_attempted(self) -> bool:
        with self._lock:
            return self._launch_attempted

    def observe_endpoint(self, online: bool) -> None:
        """Record one endpoint poll without treating endpoint-only health as success."""

        with self._lock:
            if online:
                self._offline_observations = 0
                if self._launch_attempted:
                    self._endpoint_seen_after_attempt = True
                return

            # If an attempted generation became observable and later disappeared,
            # that is a distinct outage. It is safe to arm one new recovery launch
            # after the normal debounce instead of permanently wedging recovery.
            if self._launch_attempted and self._endpoint_seen_after_attempt:
                self._launch_attempted = False
                self._endpoint_seen_after_attempt = False
                self._offline_observations = 0
            self._offline_observations += 1

    def claim_launch(self, *, require_offline_confirmation: bool) -> bool:
        """Atomically reserve the only automatic launch allowed for this episode."""

        with self._lock:
            if self._launch_attempted:
                return False
            if (
                require_offline_confirmation
                and self._offline_observations < self.offline_threshold
            ):
                return False
            self._launch_attempted = True
            self._endpoint_seen_after_attempt = False
            return True

    def record_automation_ready(self) -> None:
        """Re-arm recovery only after the full Playwright probe succeeds."""

        with self._lock:
            self._offline_observations = 0
            self._launch_attempted = False
            self._endpoint_seen_after_attempt = False


__all__ = ["BrowserRecoveryGuard"]
