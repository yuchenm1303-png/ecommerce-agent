from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import app.browser_session as bs

ROOT = Path(__file__).resolve().parents[1]


def _unique_port(seed: int = 0) -> int:
    return 42000 + ((os.getpid() * 97 + time.time_ns() + seed) % 18000)


def _run_child(code: str, *, env: dict[str, str] | None = None, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ if env is None else env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=child_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _wait_for_file(path: Path, *, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"child did not create readiness marker: {path}")


def _cleanup_port(port: int) -> None:
    for path in (
        bs._cdp_session_owner_path(port),
        bs._cdp_session_lock_path(port),
        bs._cdp_attach_lock_path(port),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    os.environ.pop(bs._cdp_session_env_key(port), None)
    bs._CDP_SESSION_LOCAL_LOCKS.pop(int(port), None)


def test_session_lease_serializes_unrelated_python_processes(tmp_path: Path) -> None:
    rounds = max(1, min(int(os.environ.get("ECOMMERCE_TEST_LAB_STRESS_ROUNDS", "3")), 100))

    for index in range(rounds):
        port = _unique_port(index)
        holder_ready = tmp_path / f"holder-{index}.ready"
        waiter_started = tmp_path / f"waiter-{index}.started"
        release_gate = tmp_path / f"holder-{index}.release"
        holder_result = tmp_path / f"holder-{index}.json"
        waiter_result = tmp_path / f"waiter-{index}.json"
        _cleanup_port(port)

        holder_code = f"""
import json, time
from pathlib import Path
from app.browser_session import acquire_cdp_session_lease
port = {port}
ready = Path({str(holder_ready)!r})
gate = Path({str(release_gate)!r})
out = Path({str(holder_result)!r})
lease = acquire_cdp_session_lease(port)
acquired = time.time()
ready.write_text('ready', encoding='utf-8')
while not gate.exists():
    time.sleep(0.01)
release_started = time.time()
lease.release()
out.write_text(json.dumps({{'acquired': acquired, 'release_started': release_started, 'released': time.time()}}), encoding='utf-8')
"""
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code],
            cwd=ROOT,
            env=dict(os.environ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        waiter: subprocess.Popen[str] | None = None
        try:
            _wait_for_file(holder_ready)
            waiter_code = f"""
import json, time
from pathlib import Path
from app.browser_session import acquire_cdp_session_lease
port = {port}
started_marker = Path({str(waiter_started)!r})
out = Path({str(waiter_result)!r})
started_marker.write_text('started', encoding='utf-8')
started = time.time()
lease = acquire_cdp_session_lease(port)
acquired = time.time()
lease.release()
out.write_text(json.dumps({{'started': started, 'acquired': acquired, 'released': time.time()}}), encoding='utf-8')
"""
            waiter = subprocess.Popen(
                [sys.executable, "-c", waiter_code],
                cwd=ROOT,
                env=dict(os.environ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _wait_for_file(waiter_started)
            time.sleep(0.08)
            assert not waiter_result.exists(), "unrelated process bypassed the root session lease"
            release_gate.write_text("release", encoding="utf-8")

            waiter_stdout, waiter_stderr = waiter.communicate(timeout=8.0)
            holder_stdout, holder_stderr = holder.communicate(timeout=8.0)
            assert holder.returncode == 0, holder_stdout + holder_stderr
            assert waiter.returncode == 0, waiter_stdout + waiter_stderr

            first = json.loads(holder_result.read_text(encoding="utf-8"))
            second = json.loads(waiter_result.read_text(encoding="utf-8"))
            assert second["acquired"] >= first["release_started"]
        finally:
            release_gate.touch(exist_ok=True)
            if waiter is not None and waiter.poll() is None:
                waiter.kill()
                waiter.communicate(timeout=2.0)
            if holder.poll() is None:
                holder.kill()
                holder.communicate(timeout=2.0)
            _cleanup_port(port)


def test_session_lease_token_is_inherited_by_real_child_process(tmp_path: Path) -> None:
    port = _unique_port(201)
    output = tmp_path / "child-inherited.json"
    _cleanup_port(port)

    root = bs.acquire_cdp_session_lease(port)
    try:
        env_key = bs._cdp_session_env_key(port)
        assert os.environ.get(env_key) == root.token
        child_code = f"""
import json, os
from pathlib import Path
from app.browser_session import acquire_cdp_session_lease, _cdp_session_env_key
port = {port}
out = Path({str(output)!r})
lease = acquire_cdp_session_lease(port)
out.write_text(json.dumps({{'inherited': lease.inherited, 'token_matches_env': lease.token == os.environ.get(_cdp_session_env_key(port))}}), encoding='utf-8')
lease.release()
"""
        child = _run_child(child_code)
        assert child.returncode == 0, child.stdout + child.stderr
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload == {"inherited": True, "token_matches_env": True}
        assert bs._read_cdp_session_owner(port).get("token") == root.token
        assert os.environ.get(env_key) == root.token
    finally:
        root.release()
        _cleanup_port(port)


def test_os_lock_recovers_after_owner_process_crash(tmp_path: Path) -> None:
    port = _unique_port(401)
    ready = tmp_path / "crash-owner.ready"
    recovered = tmp_path / "recovered.json"
    _cleanup_port(port)

    crash_code = f"""
import os
from pathlib import Path
from app.browser_session import acquire_cdp_session_lease
port = {port}
lease = acquire_cdp_session_lease(port)
Path({str(ready)!r}).write_text(lease.token, encoding='utf-8')
os._exit(17)
"""
    crashed = subprocess.Popen(
        [sys.executable, "-c", crash_code],
        cwd=ROOT,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(ready)
        crashed.communicate(timeout=4.0)
        assert crashed.returncode == 17
        stale_token = ready.read_text(encoding="utf-8")
        assert bs._cdp_session_owner_path(port).exists()

        recover_code = f"""
import json
from pathlib import Path
from app.browser_session import acquire_cdp_session_lease
port = {port}
out = Path({str(recovered)!r})
lease = acquire_cdp_session_lease(port)
out.write_text(json.dumps({{'inherited': lease.inherited, 'token': lease.token}}), encoding='utf-8')
lease.release()
"""
        child = _run_child(recover_code)
        assert child.returncode == 0, child.stdout + child.stderr
        payload = json.loads(recovered.read_text(encoding="utf-8"))
        assert payload["inherited"] is False
        assert payload["token"] != stale_token
    finally:
        if crashed.poll() is None:
            crashed.kill()
            crashed.communicate(timeout=2.0)
        _cleanup_port(port)
