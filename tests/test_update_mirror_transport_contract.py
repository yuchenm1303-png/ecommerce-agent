from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL_RELEASE = (ROOT / "supabase" / "functions" / "portal-release" / "index.ts").read_text(
    encoding="utf-8"
)
CHUNK_TRANSPORT = (ROOT / "app" / "chunked_update_transport.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
CHUNK_LIMIT_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260902054500_chunked_update_mirror_limit.sql"
).read_text(encoding="utf-8")


def test_oversized_velopack_packages_are_mirrored_as_bounded_immutable_chunks() -> None:
    assert "const MIRROR_CHUNK_BYTES = 32 * 1024 * 1024" in PORTAL_RELEASE
    assert "const MAX_CHUNKS_PER_WARM = 3" in PORTAL_RELEASE
    assert "const CHUNK_MANIFEST_SCHEMA = 1" in PORTAL_RELEASE
    assert '"Range": `bytes=${part.offset}-${end}`' in PORTAL_RELEASE
    assert "response.status !== 206" in PORTAL_RELEASE
    assert 'name: `part-${String(index).padStart(3, "0")}`' in PORTAL_RELEASE
    assert "writeChunkManifest(admin, stable, asset)" in PORTAL_RELEASE
    assert "chunkManifestReady(admin, stable, asset)" in PORTAL_RELEASE
    assert "packageMirrorReady: packagesReady" in PORTAL_RELEASE
    assert "resumableAssetToStorage" not in PORTAL_RELEASE
    assert '"Tus-Resumable"' not in PORTAL_RELEASE


def test_chunk_client_reassembles_only_validated_velopack_bytes() -> None:
    assert "class ChunkMirrorIntegrityError" in CHUNK_TRANSPORT
    assert "manifest_sha and target_sha and manifest_sha != target_sha" in CHUNK_TRANSPORT
    assert "expected_offset" in CHUNK_TRANSPORT
    assert "expected_offset != total_size" in CHUNK_TRANSPORT
    assert "digest.hexdigest().lower() != expected_sha" in CHUNK_TRANSPORT
    assert "materialize_chunked_velopack_source" in CHUNK_TRANSPORT
    assert "ProxyHandler(proxies)" in CHUNK_TRANSPORT
    assert "_RETRY_DELAYS_SECONDS = (1.0, 3.0, 7.0)" in CHUNK_TRANSPORT
    assert "from app.chunked_update_transport import" in RUNTIME
    assert "local_manager = create_update_manager(str(local_source))" in RUNTIME
    assert "local_manager.download_updates(" in RUNTIME


def test_slow_chunk_activity_keeps_worker_alive_without_fake_progress() -> None:
    assert "on_activity: Callable[[], None] | None = None" in CHUNK_TRANSPORT
    assert "if on_activity is not None:" in CHUNK_TRANSPORT
    assert "on_activity()" in CHUNK_TRANSPORT
    assert "def _heartbeat() -> None:" in CHUNK_TRANSPORT
    assert "legitimately slow 32 MB transfer" in CHUNK_TRANSPORT
    assert "copied += _append_verified_chunk(" in CHUNK_TRANSPORT
    assert "int(copied * 100 / total_size)" in CHUNK_TRANSPORT


def test_package_mirror_has_a_recoverable_cross_instance_lock() -> None:
    assert "const MIRROR_LOCK_MAX_AGE_MS = 15 * 60 * 1000" in PORTAL_RELEASE
    assert 'return `${mirrorFolder(version)}/.package-mirror.lock`' in PORTAL_RELEASE
    assert "upsert: false" in PORTAL_RELEASE
    assert "Date.now() - createdAt < MIRROR_LOCK_MAX_AGE_MS" in PORTAL_RELEASE
    assert "await releaseMirrorLock(admin, stable.version)" in PORTAL_RELEASE


def test_package_warmup_continues_automatically_but_is_hard_bounded() -> None:
    assert "const MAX_AUTOMATIC_WARM_PASSES = 12" in PORTAL_RELEASE
    assert 'const WARM_PASS_QUERY = "warm_pass"' in PORTAL_RELEASE
    assert "function requiredWarmPasses(stable: StableRelease): number" in PORTAL_RELEASE
    assert "Math.ceil(chunkPlan(asset).length / MAX_CHUNKS_PER_WARM)" in PORTAL_RELEASE
    assert "Math.min(required, MAX_AUTOMATIC_WARM_PASSES)" in PORTAL_RELEASE
    assert "if (!completed || await packageMirrorReady(admin, stable)) return" in PORTAL_RELEASE
    assert "nextPass >= requiredPasses || nextPass >= MAX_AUTOMATIC_WARM_PASSES" in PORTAL_RELEASE
    assert "nextUrl.searchParams.set(WARM_PASS_QUERY, String(nextPass))" in PORTAL_RELEASE
    assert "EdgeRuntime.waitUntil(warmAndContinue(req, admin, stable, warmPass))" in PORTAL_RELEASE


def test_storage_bucket_limit_matches_free_plan_chunk_transport_boundary() -> None:
    assert "file_size_limit = 52428800" in CHUNK_LIMIT_MIGRATION
    assert "public = true" in CHUNK_LIMIT_MIGRATION
    assert "application/json" in CHUNK_LIMIT_MIGRATION
    assert "application/octet-stream" in CHUNK_LIMIT_MIGRATION
