from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTAL_RELEASE = ROOT / "supabase" / "functions" / "portal-release" / "index.ts"
PORTAL_DOWNLOAD = ROOT / "supabase" / "functions" / "portal-download" / "index.ts"
PORTAL_WARM = ROOT / "supabase" / "functions" / "portal-release-warm" / "index.ts"
PRIVATE_BUCKET_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260905070000_listing_studio_private_download_bucket.sql"
)
WARM_WORKFLOW = ROOT / ".github" / "workflows" / "release-mirror-warm.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_release_control_plane_has_no_private_github_fallback():
    source = read(PORTAL_RELEASE)
    assert "GITHUB_UPDATE_SOURCE" not in source
    assert "api.github.com" not in source
    assert "github.com/" not in source
    assert 'updateSources: [baseUrl]' in source
    assert "loadStableCache" in source
    assert "releaseMirrorReady" in source


def test_authorized_download_returns_signed_private_chunks_not_github_urls():
    source = read(PORTAL_DOWNLOAD)
    assert 'const DOWNLOAD_BUCKET = "listing-studio-downloads"' in source
    assert ".createSignedUrls(" in source
    assert 'delivery: "chunked"' in source
    assert "github.com/" not in source
    assert "browser_download_url" not in source
    assert "window.location" not in source


def test_publish_warmer_is_the_only_authenticated_private_github_bridge():
    source = read(PORTAL_WARM)
    assert 'const REPOSITORY = "yuchenm1303-png/ecommerce-agent"' in source
    assert "Authorization: `Bearer ${token}`" in source
    assert "releases/assets/${asset.id}" in source
    assert 'const DOWNLOAD_BUCKET = "listing-studio-downloads"' in source
    assert "MAX_CHUNKS_PER_CALL = 3" in source
    assert "if (ready)" in source
    assert "STABLE_CACHE_PATH" in source


def test_installer_storage_is_private_and_bounded_under_project_object_limit():
    migration = read(PRIVATE_BUCKET_MIGRATION)
    assert "'listing-studio-downloads'" in migration
    assert "public, file_size_limit" in migration
    assert "false, 52428800" in migration
    assert "set public = false" in migration.lower()


def test_release_mirror_workflow_uses_only_ephemeral_actions_token():
    workflow = read(WARM_WORKFLOW)
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "portal-release-warm" in workflow
    assert "Authorization: Bearer ${GH_TOKEN}" in workflow
    assert "contents: read" in workflow
    assert "PAT" not in workflow
