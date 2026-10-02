from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Read-only public Stable authority: serves metadata only for a complete mirror.
PORTAL_RELEASE = (ROOT / "supabase" / "functions" / "portal-release" / "index.ts").read_text(
    encoding="utf-8"
)
# Mirror writer, invoked by the publish pipeline / scheduled warm workflow.
PORTAL_RELEASE_WARM = (
    ROOT / "supabase" / "functions" / "portal-release-warm" / "index.ts"
).read_text(encoding="utf-8")
WARM_WORKFLOW = (ROOT / ".github" / "workflows" / "release-mirror-warm.yml").read_text(
    encoding="utf-8"
)
CHUNK_TRANSPORT = (ROOT / "app" / "chunked_update_transport.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")
CHUNK_LIMIT_MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260902054500_chunked_update_mirror_limit.sql"
).read_text(encoding="utf-8")


def test_oversized_velopack_packages_are_mirrored_as_bounded_immutable_chunks() -> None:
    # Writer: ranged 32 MB immutable parts, at most three new parts per call.
    assert "const CHUNK_BYTES = 32 * 1024 * 1024" in PORTAL_RELEASE_WARM
    assert "const MAX_CHUNKS_PER_CALL = 3" in PORTAL_RELEASE_WARM
    assert "const CHUNK_MANIFEST_SCHEMA = 1" in PORTAL_RELEASE_WARM
    assert "Range: `bytes=${part.offset}-${end}`" in PORTAL_RELEASE_WARM
    assert "response.status !== 206" in PORTAL_RELEASE_WARM
    assert 'name: `part-${String(index).padStart(3, "0")}`' in PORTAL_RELEASE_WARM
    assert "await writeManifest(admin, bucket, folder, version, asset)" in PORTAL_RELEASE_WARM
    # Reader: same chunk size/schema; a package is ready only through its manifest.
    assert "const MIRROR_CHUNK_BYTES = 32 * 1024 * 1024" in PORTAL_RELEASE
    assert "const CHUNK_MANIFEST_SCHEMA = 1" in PORTAL_RELEASE
    assert "return await chunkManifestReady(admin, stable, asset)" in PORTAL_RELEASE
    assert "if (!(await releaseMirrorReady(admin, stable)))" in PORTAL_RELEASE
    assert "packageMirrorReady: true" in PORTAL_RELEASE
    for source in (PORTAL_RELEASE, PORTAL_RELEASE_WARM):
        assert "resumableAssetToStorage" not in source
        assert '"Tus-Resumable"' not in source


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


def test_package_mirror_warmup_is_single_flight_and_idempotently_recoverable() -> None:
    # The public reader never writes mirror objects, so client traffic cannot race
    # the writer; it fails closed until the complete mirror exists.
    assert ".upload(" not in PORTAL_RELEASE
    assert "throw new Error(`stable_mirror_incomplete:v${stable.version}`)" in PORTAL_RELEASE
    assert 'json(req, { error: "stable_release_unavailable" }, 503)' in PORTAL_RELEASE
    # One warmer at a time; an interrupted run is never cancelled mid-upload.
    assert "group: warm-stable-mirrors" in WARM_WORKFLOW
    assert "cancel-in-progress: false" in WARM_WORKFLOW
    # Recovery is idempotent: verified parts are skipped, wrong-size leftovers are
    # replaced, and every new part is re-verified before the manifest is written.
    assert "if (await objectReady(admin, bucket, folder, part.name, part.size)) continue;" in PORTAL_RELEASE_WARM
    assert "await removeIfWrongSize(admin, bucket, folder, part.name, part.size);" in PORTAL_RELEASE_WARM
    assert "throw new Error(`chunk_verify_failed:${asset.name}:${part.name}`)" in PORTAL_RELEASE_WARM


def test_package_warmup_continues_automatically_but_is_hard_bounded() -> None:
    # Each call is budgeted; the workflow continues automatically after publish
    # and on a schedule, but every run is bounded.
    assert "const budget = { remaining: MAX_CHUNKS_PER_CALL };" in PORTAL_RELEASE_WARM
    assert "if (budget.remaining <= 0) return false;" in PORTAL_RELEASE_WARM
    assert "chunksRemainingThisCall: budget.remaining" in PORTAL_RELEASE_WARM
    assert '- "Publish Update"' in WARM_WORKFLOW
    assert 'cron: "*/5 * * * *"' in WARM_WORKFLOW
    assert "for attempt in $(seq 1 12); do" in WARM_WORKFLOW
    assert 'if [[ "$ready" == "true" ]]; then' in WARM_WORKFLOW
    assert "did not become ready after 12 bounded attempts" in WARM_WORKFLOW
    assert "timeout-minutes: 20" in WARM_WORKFLOW


def test_storage_bucket_limit_matches_free_plan_chunk_transport_boundary() -> None:
    assert "file_size_limit = 52428800" in CHUNK_LIMIT_MIGRATION
    assert "public = true" in CHUNK_LIMIT_MIGRATION
    assert "application/json" in CHUNK_LIMIT_MIGRATION
    assert "application/octet-stream" in CHUNK_LIMIT_MIGRATION
