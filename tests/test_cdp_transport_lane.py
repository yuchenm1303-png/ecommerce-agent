from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.cdp_transport_lane import _transport_lane_lock_path


ROOT = Path(__file__).resolve().parents[1]


def _unique_port() -> int:
    return 47000 + ((os.getpid() * 131 + time.time_ns()) % 12000)


def _wait(path: Path, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def test_sibling_processes_cannot_hold_same_transport_lane_together(tmp_path: Path) -> None:
    port = _unique_port()
    ready = tmp_path / "holder.ready"
    release = tmp_path / "holder.release"
    first = tmp_path / "holder.json"
    second = tmp_path / "waiter.json"

    try:
        _transport_lane_lock_path(port).unlink()
    except OSError:
        pass

    holder_code = f"""
import json, time
from pathlib import Path
from app.cdp_transport_lane import exclusive_cdp_transport_lane
port={port}
ready=Path({str(ready)!r})
release=Path({str(release)!r})
out=Path({str(first)!r})
with exclusive_cdp_transport_lane(port):
    acquired=time.time()
    ready.write_text('ready', encoding='utf-8')
    while not release.exists():
        time.sleep(0.01)
    released=time.time()
out.write_text(json.dumps({{'acquired': acquired, 'release_started': released}}), encoding='utf-8')
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    waiter = None
    try:
        _wait(ready)
        waiter_code = f"""
import json, time
from pathlib import Path
from app.cdp_transport_lane import exclusive_cdp_transport_lane
port={port}
out=Path({str(second)!r})
started=time.time()
with exclusive_cdp_transport_lane(port):
    acquired=time.time()
out.write_text(json.dumps({{'started': started, 'acquired': acquired}}), encoding='utf-8')
"""
        waiter = subprocess.Popen(
            [sys.executable, "-c", waiter_code],
            cwd=ROOT,
            env=dict(os.environ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.12)
        assert not second.exists(), "sibling worker bypassed the exclusive transport lane"

        release.write_text("release", encoding="utf-8")
        waiter_stdout, waiter_stderr = waiter.communicate(timeout=8.0)
        holder_stdout, holder_stderr = holder.communicate(timeout=8.0)
        assert holder.returncode == 0, holder_stdout + holder_stderr
        assert waiter.returncode == 0, waiter_stdout + waiter_stderr

        holder_result = json.loads(first.read_text(encoding="utf-8"))
        waiter_result = json.loads(second.read_text(encoding="utf-8"))
        assert waiter_result["acquired"] >= holder_result["release_started"]
    finally:
        release.touch(exist_ok=True)
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
            waiter.communicate(timeout=2.0)
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=2.0)
        try:
            _transport_lane_lock_path(port).unlink()
        except OSError:
            pass


def test_nested_process_tree_lane_fails_fast(tmp_path: Path) -> None:
    port = _unique_port()
    output = tmp_path / "nested.json"
    code = f"""
import json, subprocess, sys
from pathlib import Path
from app.cdp_transport_lane import exclusive_cdp_transport_lane
port={port}
out=Path({str(output)!r})
with exclusive_cdp_transport_lane(port):
    child=subprocess.run(
        [sys.executable, '-c', 'from app.cdp_transport_lane import exclusive_cdp_transport_lane;\\nwith exclusive_cdp_transport_lane({port}):\\n    pass'],
        text=True,
        capture_output=True,
        check=False,
    )
out.write_text(json.dumps({{'returncode': child.returncode, 'stderr': child.stderr, 'stdout': child.stdout}}), encoding='utf-8')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=8.0,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["returncode"] != 0
    assert "禁止嵌套" in payload["stderr"]
