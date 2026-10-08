from __future__ import annotations

import ast
import hashlib
import io
import socket
import ssl
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import chunked_update_transport as chunks

ROOT = Path(__file__).resolve().parents[1]


class PartialDisconnect:
    status = 200
    headers = {}

    def __init__(self, data: bytes):
        self.data = data
        self.first = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, length: int) -> bytes:
        if self.first:
            self.first = False
            return self.data
        raise urllib.error.URLError("connection reset mid stream")


class MemoryResponse(io.BytesIO):
    def __init__(self, data: bytes, *, status: int, headers: dict[str, str] | None = None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


@pytest.fixture
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(chunks, "_retry_delay", lambda _: None)


def test_chunk_resumes_at_last_committed_byte_after_disconnect(tmp_path, monkeypatch, no_retry_delay):
    data = b"0123456789"
    offsets = []

    def open_source(url, *, range_start=0):
        offsets.append(range_start)
        if range_start == 0:
            return PartialDisconnect(data[:4])
        assert range_start == 4
        return MemoryResponse(data[4:], status=206, headers={"Content-Range": "bytes 4-9/10"})

    monkeypatch.setattr(chunks, "_open", open_source)
    target = tmp_path / "mirror.part"
    chunks._download_exact_object("https://example.invalid/part", target, expected_size=10, expected_sha256=hashlib.sha256(data).hexdigest())
    assert target.read_bytes() == data
    assert offsets == [0, 4]


def test_chunk_restarts_if_cdn_ignores_range_header(tmp_path, monkeypatch, no_retry_delay):
    data = b"abcdefghij"
    offsets = []

    def open_source(url, *, range_start=0):
        offsets.append(range_start)
        if range_start == 0:
            return PartialDisconnect(data[:6])
        return MemoryResponse(data, status=200)

    monkeypatch.setattr(chunks, "_open", open_source)
    target = tmp_path / "mirror.part"
    chunks._download_exact_object("https://example.invalid/part", target, expected_size=10, expected_sha256=hashlib.sha256(data).hexdigest())
    assert target.read_bytes() == data
    assert offsets == [0, 6]


def test_wrong_content_range_is_discarded_before_retry(tmp_path, monkeypatch, no_retry_delay):
    data = b"0123456789"
    offsets = []

    def open_source(url, *, range_start=0):
        offsets.append(range_start)
        if len(offsets) == 1:
            return PartialDisconnect(data[:4])
        if len(offsets) == 2:
            return MemoryResponse(data[4:], status=206, headers={"Content-Range": "bytes 2-9/10"})
        assert range_start == 0
        return MemoryResponse(data, status=200)

    monkeypatch.setattr(chunks, "_open", open_source)
    target = tmp_path / "mirror.part"
    chunks._download_exact_object("https://example.invalid/part", target, expected_size=10, expected_sha256=hashlib.sha256(data).hexdigest())
    assert target.read_bytes() == data
    assert offsets == [0, 4, 0]


def test_complete_wrong_sha_is_not_accepted(tmp_path, monkeypatch, no_retry_delay):
    data = b"bad content"
    monkeypatch.setattr(chunks, "_open", lambda *_args, **_kwargs: MemoryResponse(data, status=200))
    target = tmp_path / "mirror.part"
    with pytest.raises(chunks.ChunkMirrorIntegrityError, match="SHA256 mismatch"):
        chunks._download_exact_object(
            "https://example.invalid/part", target,
            expected_size=len(data),
            expected_sha256=hashlib.sha256(b"good").hexdigest(),
        )
    assert not target.exists()


def test_source_get_uses_range_and_explicit_proxy_handler(monkeypatch):
    requests = []

    class FakeOpener:
        def open(self, request, timeout):
            requests.append((request.get_header("Range"), timeout))
            return MemoryResponse(b"ok", status=206)

    monkeypatch.setattr(chunks.urllib.request, "build_opener", lambda *args: FakeOpener())
    chunks._open("https://example.invalid/chunk", range_start=13)
    assert requests == [("bytes=13-", chunks._HTTP_TIMEOUT_SECONDS)]


def test_cross_border_client_timeouts_are_relaxed_but_bounded():
    code = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
    auth = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
    oauth = (ROOT / "app" / "desktop_oauth.py").read_text(encoding="utf-8")
    assert "_UPDATE_CHECK_TIMEOUT_SECONDS = 36.0" in code
    assert "_UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 110.0" in code
    assert "_HTTP_TIMEOUT_SECONDS = 22" in auth
    assert "_OAUTH_TIMEOUT_SECONDS = 300" in oauth


def test_rotated_refresh_is_persisted_before_license_validation():
    source = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    restore = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_restore_session")
    method_text = ast.get_source_segment(source, restore)
    assert method_text.index("_save_state(interim)") < method_text.index("licensed = _license_check(")
    assert "refresh_token=current_refresh_token" in method_text
    assert "except AccessNetworkError:" in method_text
    controller = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ApplicationAccessController")
    refresh = next(n for n in controller.body if isinstance(n, ast.FunctionDef) and n.name == "_refresh_online")
    method_text = ast.get_source_segment(source, refresh)
    assert method_text.index("_save_state(self.session)") < method_text.index("licensed = _license_check(")


def test_auth_refresh_remains_non_retried_and_device_validation_is_retryable():
    source = (ROOT / "gui" / "app_access.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    auth = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_auth_refresh")
    license = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_license_check")
    assert "retry_transient" not in ast.get_source_segment(source, auth)
    assert 'retry_transient=action == "validate"' in ast.get_source_segment(source, license)


def _isolate_chunk_priority_manager(context):
    import __future__
    source = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
    parsed = ast.parse(source)
    node = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "_download_with_chunked_fallback")
    unit = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = dict(context)
    exec(
        compile(unit, "app/velopack_runtime.py", "exec", flags=__future__.annotations.compiler_flag),
        namespace,
    )
    return namespace["_download_with_chunked_fallback"]


def test_full_only_mirror_prefers_resumable_chunks_over_large_direct_package(tmp_path):
    from contextlib import contextmanager

    calls = []
    info = SimpleNamespace(
        TargetFullRelease=SimpleNamespace(Version="0.1.67"),
        DeltasToTarget=[],
    )

    class Direct:
        def download_updates(self, *_):
            calls.append("direct")

    class Local:
        def check_for_updates(self):
            calls.append("local-check")
            return info

        def download_updates(self, *_):
            calls.append("local-download")

        def get_update_pending_restart(self):
            return object()

    local = Local()

    @contextmanager
    def mirrored(*args, **kwargs):
        calls.append("chunks")
        yield tmp_path

    fn = _isolate_chunk_priority_manager({
        "GITHUB_REPOSITORY_URL": "https://github.com/example/repo",
        "ChunkMirrorUnavailable": type("ChunkMirrorUnavailable", (RuntimeError,), {}),
        "materialize_chunked_velopack_source": mirrored,
        "create_update_manager": lambda _: local,
    })
    result = fn(Direct(), info, "https://mirror.example/stable/v0.1.67", lambda value: None)
    assert result is local
    assert calls == ["chunks", "local-check", "local-download"]


def test_small_delta_remains_direct_velopack_first(tmp_path):
    from contextlib import contextmanager

    calls = []
    info = SimpleNamespace(
        TargetFullRelease=SimpleNamespace(Version="0.1.67"),
        DeltasToTarget=[SimpleNamespace(FileName="patch.nupkg")],
    )

    class Direct:
        def download_updates(self, *_):
            calls.append("direct")

    @contextmanager
    def mirrored(*args, **kwargs):
        calls.append("chunks")
        yield tmp_path

    direct = Direct()
    fn = _isolate_chunk_priority_manager({
        "GITHUB_REPOSITORY_URL": "https://github.com/example/repo",
        "ChunkMirrorUnavailable": type("ChunkMirrorUnavailable", (RuntimeError,), {}),
        "materialize_chunked_velopack_source": mirrored,
    })
    assert fn(direct, info, "https://mirror.example", lambda value: None) is direct
    assert calls == ["direct"]


def test_unavailable_full_mirror_falls_back_to_normal_velopack_transport():
    from contextlib import contextmanager

    calls = []
    unavailable = type("ChunkMirrorUnavailable", (RuntimeError,), {})
    info = SimpleNamespace(
        TargetFullRelease=SimpleNamespace(Version="0.1.67"),
        DeltasToTarget=[],
    )

    class Direct:
        def download_updates(self, *_):
            calls.append("direct")

    @contextmanager
    def mirrored(*args, **kwargs):
        calls.append("chunks")
        raise unavailable("not yet mirrored")
        yield

    fn = _isolate_chunk_priority_manager({
        "GITHUB_REPOSITORY_URL": "https://github.com/example/repo",
        "ChunkMirrorUnavailable": unavailable,
        "materialize_chunked_velopack_source": mirrored,
    })
    direct = Direct()
    assert fn(direct, info, "https://mirror.example", lambda value: None) is direct
    assert calls == ["chunks", "direct"]
