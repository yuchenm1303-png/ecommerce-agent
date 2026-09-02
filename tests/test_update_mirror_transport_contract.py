from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL_RELEASE = (ROOT / "supabase" / "functions" / "portal-release" / "index.ts").read_text(
    encoding="utf-8"
)


def test_large_velopack_mirror_uses_supabase_tus_resumable_uploads() -> None:
    assert "const STANDARD_UPLOAD_LIMIT = 6 * 1024 * 1024" in PORTAL_RELEASE
    assert "const TUS_CHUNK_SIZE = 6 * 1024 * 1024" in PORTAL_RELEASE
    assert ".storage.supabase.co" in PORTAL_RELEASE
    assert '"Tus-Resumable": "1.0.0"' in PORTAL_RELEASE
    assert '"Upload-Length": String(asset.size)' in PORTAL_RELEASE
    assert '"Upload-Metadata": metadata' in PORTAL_RELEASE
    assert '"Content-Type": "application/offset+octet-stream"' in PORTAL_RELEASE
    assert '"Upload-Offset": String(offset)' in PORTAL_RELEASE
    assert '"Range": `bytes=${offset}-${end}`' in PORTAL_RELEASE
    assert "response.status !== 206" in PORTAL_RELEASE
    assert "resumableAssetToStorage(version, asset)" in PORTAL_RELEASE


def test_package_mirror_has_a_recoverable_cross_instance_lock() -> None:
    assert 'const MIRROR_LOCK_MAX_AGE_MS = 15 * 60 * 1000' in PORTAL_RELEASE
    assert 'return `${mirrorFolder(version)}/.package-mirror.lock`' in PORTAL_RELEASE
    assert 'upsert: false' in PORTAL_RELEASE
    assert 'Date.now() - createdAt < MIRROR_LOCK_MAX_AGE_MS' in PORTAL_RELEASE
    assert 'await releaseMirrorLock(admin, stable.version)' in PORTAL_RELEASE
