import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const GITHUB_UPDATE_SOURCE = `https://github.com/${REPOSITORY}`;
const LATEST_RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const RELEASE_HISTORY_API = `https://api.github.com/repos/${REPOSITORY}/releases?per_page=20`;
const UPDATE_BUCKET = "listing-studio-updates";
const UPDATE_CHANNEL = "win-x64-stable";
const UPDATE_FEED_NAME = `releases.${UPDATE_CHANNEL}.json`;
const STABLE_CACHE_PATH = "stable/latest.json";
const LEGACY_MANIFEST_ASSET = "update.json";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_DIGEST_RE = /^sha256:([0-9a-f]{64})$/i;
const HISTORY_LIMIT = 12;
const STANDARD_UPLOAD_LIMIT = 6 * 1024 * 1024;
const TUS_CHUNK_SIZE = 6 * 1024 * 1024;
const MIRROR_LOCK_MAX_AGE_MS = 15 * 60 * 1000;
const ALLOWED_ORIGINS = new Set([
  "https://smirel.com",
  "https://www.smirel.com",
]);

type MirrorAsset = {
  name: string;
  sourceUrl: string;
  size: number;
  contentType: string;
};

type StableRelease = {
  version: string;
  title: string;
  notes: string;
  publishedAt: string;
  required: boolean;
  minSupportedVersion: string;
  installerName: string;
  installerUrl: string;
  installerSha256: string;
  installerSize: number;
  fileSize: string;
  feedAsset: MirrorAsset;
  packageAssets: MirrorAsset[];
};

function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("origin") || "";
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGINS.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function json(req: Request, body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(req),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": status === 200 ? "public, max-age=30, s-maxage=60" : "no-store",
    },
  });
}

function githubHeaders(userAgent: string): Record<string, string> {
  return {
    "Accept": "application/vnd.github+json",
    "User-Agent": userAgent,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function normalizeVersion(value: unknown): string {
  const raw = String(value ?? "").trim();
  return raw.startsWith("v") ? raw.slice(1) : raw;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const mib = bytes / 1024 / 1024;
  return `${mib >= 100 ? mib.toFixed(0) : mib.toFixed(1)} MB`;
}

function getServerSecretKey(): string {
  const secretBundle = Deno.env.get("SUPABASE_SECRET_KEYS") || "";
  if (secretBundle) {
    try {
      const parsed = JSON.parse(secretBundle);
      const value = String(parsed?.default || "").trim();
      if (value) return value;
    } catch (error) {
      console.error("failed to parse SUPABASE_SECRET_KEYS", error);
    }
  }
  return String(Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
}

function storageConfig() {
  const supabaseUrl = String(Deno.env.get("SUPABASE_URL") || "").trim().replace(/\/$/, "");
  const serverKey = getServerSecretKey();
  if (!supabaseUrl || !serverKey) throw new Error("server_storage_config_missing");
  const projectId = new URL(supabaseUrl).hostname.split(".")[0];
  if (!projectId) throw new Error("server_storage_project_missing");
  const directStorageUrl = `https://${projectId}.storage.supabase.co`;
  return { supabaseUrl, serverKey, directStorageUrl };
}

function createAdminClient() {
  const { supabaseUrl, serverKey } = storageConfig();
  return createClient(supabaseUrl, serverKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function assetForRelease(release: any, version: string, name: string, contentType: string): MirrorAsset | null {
  const asset = release.assets.find((candidate: any) => candidate?.name === name);
  const size = Number(asset?.size || 0);
  const expectedUrl = `https://github.com/${REPOSITORY}/releases/download/v${version}/${name}`;
  if (
    String(asset?.browser_download_url || "") !== expectedUrl ||
    !Number.isSafeInteger(size) ||
    size <= 0
  ) {
    return null;
  }
  return { name, sourceUrl: expectedUrl, size, contentType };
}

function parseStableRelease(release: any): StableRelease | null {
  if (!release || release.draft || release.prerelease || !Array.isArray(release.assets)) {
    return null;
  }

  const version = normalizeVersion(release.tag_name);
  if (!VERSION_RE.test(version) || String(release.tag_name || "") !== `v${version}`) {
    return null;
  }

  const installerName = `EcommerceAgent-Setup-${version}.exe`;
  const installerAsset = release.assets.find((asset: any) => asset?.name === installerName);
  const installerSize = Number(installerAsset?.size || 0);
  const digest = String(installerAsset?.digest || "").trim().toLowerCase();
  const digestMatch = digest.match(SHA256_DIGEST_RE);
  const installerUrl = `https://github.com/${REPOSITORY}/releases/download/v${version}/${installerName}`;
  if (
    String(installerAsset?.browser_download_url || "") !== installerUrl ||
    !Number.isSafeInteger(installerSize) ||
    installerSize <= 0 ||
    !digestMatch
  ) {
    return null;
  }

  const feedAsset = assetForRelease(release, version, UPDATE_FEED_NAME, "application/json");
  const fullName = `Smirel.ListingStudio-${version}-${UPDATE_CHANNEL}-full.nupkg`;
  const deltaName = `Smirel.ListingStudio-${version}-${UPDATE_CHANNEL}-delta.nupkg`;
  const fullAsset = assetForRelease(release, version, fullName, "application/octet-stream");
  const deltaAsset = assetForRelease(release, version, deltaName, "application/octet-stream");
  if (!feedAsset || !fullAsset) return null;

  return {
    version,
    title: String(release.name || `Listing Studio ${version}`).trim() || `Listing Studio ${version}`,
    notes: String(release.body || "").trim(),
    publishedAt: String(release.published_at || release.created_at || "").trim(),
    required: false,
    minSupportedVersion: "",
    installerName,
    installerUrl,
    installerSha256: digestMatch[1].toLowerCase(),
    installerSize,
    fileSize: formatBytes(installerSize),
    feedAsset,
    packageAssets: deltaAsset ? [deltaAsset, fullAsset] : [fullAsset],
  };
}

async function applyLegacyMetadata(stable: StableRelease, release: any): Promise<StableRelease> {
  const legacyManifestAsset = release.assets.find((asset: any) => asset?.name === LEGACY_MANIFEST_ASSET);
  if (!legacyManifestAsset?.browser_download_url) return stable;

  try {
    const manifestResponse = await fetch(String(legacyManifestAsset.browser_download_url), {
      headers: { "User-Agent": "Listing-Studio-Release-Metadata" },
      cache: "no-store",
      redirect: "follow",
    });
    if (!manifestResponse.ok) return stable;

    const manifest = await manifestResponse.json();
    if (
      manifest?.schema_version !== 1 ||
      manifest?.channel !== "stable" ||
      normalizeVersion(manifest?.version) !== stable.version
    ) {
      return stable;
    }

    return {
      ...stable,
      title: String(manifest?.title || stable.title).trim() || stable.title,
      notes: String(manifest?.notes || stable.notes).trim(),
      publishedAt: String(manifest?.published_at || stable.publishedAt).trim(),
      required: Boolean(manifest?.required),
      minSupportedVersion: String(manifest?.min_supported_version || "").trim(),
    };
  } catch (error) {
    console.error("legacy release metadata ignored", error);
    return stable;
  }
}

async function resolveStableReleaseFromGitHub(): Promise<StableRelease> {
  const releaseResponse = await fetch(LATEST_RELEASE_API, {
    headers: githubHeaders("Listing-Studio-Release-Metadata"),
    cache: "no-store",
  });
  if (!releaseResponse.ok) throw new Error(`release_api_${releaseResponse.status}`);

  const release = await releaseResponse.json();
  const parsed = parseStableRelease(release);
  if (!parsed) throw new Error("invalid_latest_release");
  return await applyLegacyMetadata(parsed, release);
}

async function persistStableCache(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  const blob = new Blob([JSON.stringify(stable)], { type: "application/json" });
  const { error } = await admin.storage.from(UPDATE_BUCKET).upload(STABLE_CACHE_PATH, blob, {
    contentType: "application/json",
    cacheControl: "30",
    upsert: true,
  });
  if (error) throw error;
}

async function loadStableCache(admin: ReturnType<typeof createAdminClient>): Promise<StableRelease> {
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).download(STABLE_CACHE_PATH);
  if (error || !data) throw error || new Error("stable_cache_missing");
  const parsed = JSON.parse(await data.text());
  if (!parsed || !VERSION_RE.test(String(parsed.version || ""))) throw new Error("stable_cache_invalid");
  if (!parsed.feedAsset || !Array.isArray(parsed.packageAssets)) throw new Error("stable_cache_invalid_assets");
  return parsed as StableRelease;
}

async function resolveStableRelease(admin: ReturnType<typeof createAdminClient>): Promise<StableRelease> {
  try {
    const stable = await resolveStableReleaseFromGitHub();
    await persistStableCache(admin, stable).catch((error) => console.error("stable cache persist failed", error));
    return stable;
  } catch (githubError) {
    console.error("GitHub Stable control-plane lookup failed; using last validated cache", githubError);
    return await loadStableCache(admin);
  }
}

async function resolveStableHistory(currentVersion: string) {
  const response = await fetch(RELEASE_HISTORY_API, {
    headers: githubHeaders("Listing-Studio-Release-History"),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`release_history_api_${response.status}`);

  const releases = await response.json();
  if (!Array.isArray(releases)) throw new Error("invalid_release_history");

  return releases
    .map(parseStableRelease)
    .filter((item): item is StableRelease => Boolean(item))
    .filter((item) => item.version !== currentVersion)
    .sort((a, b) => Date.parse(b.publishedAt || "") - Date.parse(a.publishedAt || ""))
    .slice(0, HISTORY_LIMIT)
    .map((item) => ({
      version: `v${item.version}`,
      title: item.title,
      notes: item.notes,
      publishedAt: item.publishedAt,
      fileSize: item.fileSize,
      fileSizeBytes: item.installerSize,
      installerSha256: item.installerSha256,
    }));
}

function mirrorFolder(version: string): string {
  return `stable/v${version}`;
}

function mirrorBaseUrl(version: string): string {
  const { supabaseUrl } = storageConfig();
  return `${supabaseUrl}/storage/v1/object/public/${UPDATE_BUCKET}/${mirrorFolder(version)}`;
}

async function mirrorAssetExists(
  admin: ReturnType<typeof createAdminClient>,
  version: string,
  asset: MirrorAsset,
): Promise<boolean> {
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).list(mirrorFolder(version), {
    limit: 100,
    search: asset.name,
  });
  if (error) throw error;
  const hit = data?.find((item: any) => item?.name === asset.name);
  if (!hit) return false;
  const size = Number(hit?.metadata?.size ?? hit?.metadata?.contentLength ?? 0);
  return size === asset.size;
}

function encodedObjectPath(path: string): string {
  return path.split("/").map((part) => encodeURIComponent(part)).join("/");
}

function base64Metadata(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function retry<T>(label: string, action: () => Promise<T>, attempts = 3): Promise<T> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await action();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
    }
  }
  throw new Error(`${label}:${String((lastError as any)?.message || lastError || "failed")}`);
}

async function standardStreamAssetToStorage(version: string, asset: MirrorAsset): Promise<void> {
  const source = await fetch(asset.sourceUrl, {
    headers: { "User-Agent": "Listing-Studio-Update-Mirror" },
    cache: "no-store",
    redirect: "follow",
  });
  if (!source.ok || !source.body) throw new Error(`update_asset_fetch_${source.status}:${asset.name}`);
  const sourceLength = Number(source.headers.get("content-length") || 0);
  if (sourceLength > 0 && sourceLength !== asset.size) {
    try { await source.body.cancel(); } catch {}
    throw new Error(`update_asset_source_size_mismatch:${asset.name}`);
  }

  const { supabaseUrl, serverKey } = storageConfig();
  const path = `${mirrorFolder(version)}/${asset.name}`;
  const uploadUrl = `${supabaseUrl}/storage/v1/object/${UPDATE_BUCKET}/${encodedObjectPath(path)}`;
  const init = {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${serverKey}`,
      "apikey": serverKey,
      "Content-Type": asset.contentType,
      "Content-Length": String(asset.size),
      "Cache-Control": "max-age=31536000, immutable",
      "x-upsert": "true",
    },
    body: source.body,
    duplex: "half",
  } as RequestInit & { duplex: "half" };
  const upload = await fetch(uploadUrl, init);
  if (!upload.ok) {
    const detail = (await upload.text()).slice(0, 800);
    throw new Error(`update_asset_upload_${upload.status}:${asset.name}:${detail}`);
  }
}

async function createTusUpload(version: string, asset: MirrorAsset): Promise<string> {
  const { directStorageUrl, serverKey } = storageConfig();
  const endpoint = `${directStorageUrl}/storage/v1/upload/resumable`;
  const objectName = `${mirrorFolder(version)}/${asset.name}`;
  const metadata = [
    ["bucketName", UPDATE_BUCKET],
    ["objectName", objectName],
    ["contentType", asset.contentType],
    ["cacheControl", "31536000"],
  ].map(([key, value]) => `${key} ${base64Metadata(value)}`).join(",");

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${serverKey}`,
      "apikey": serverKey,
      "Tus-Resumable": "1.0.0",
      "Upload-Length": String(asset.size),
      "Upload-Metadata": metadata,
      "x-upsert": "true",
    },
  });
  if (response.status !== 201) {
    throw new Error(`tus_create_${response.status}:${(await response.text()).slice(0, 500)}`);
  }
  const location = String(response.headers.get("location") || "").trim();
  if (!location) throw new Error("tus_create_missing_location");
  return new URL(location, endpoint).toString();
}

async function readTusOffset(uploadUrl: string): Promise<number> {
  const { serverKey } = storageConfig();
  const response = await fetch(uploadUrl, {
    method: "HEAD",
    headers: {
      "Authorization": `Bearer ${serverKey}`,
      "apikey": serverKey,
      "Tus-Resumable": "1.0.0",
    },
  });
  if (!response.ok) throw new Error(`tus_head_${response.status}`);
  const offset = Number(response.headers.get("upload-offset") || -1);
  if (!Number.isSafeInteger(offset) || offset < 0) throw new Error("tus_head_invalid_offset");
  return offset;
}

async function fetchSourceChunk(asset: MirrorAsset, offset: number): Promise<Uint8Array> {
  const end = Math.min(asset.size - 1, offset + TUS_CHUNK_SIZE - 1);
  const expected = end - offset + 1;
  return await retry(`source_range_${offset}`, async () => {
    const response = await fetch(asset.sourceUrl, {
      headers: {
        "User-Agent": "Listing-Studio-Update-Mirror",
        "Range": `bytes=${offset}-${end}`,
      },
      cache: "no-store",
      redirect: "follow",
    });
    if (response.status !== 206) {
      throw new Error(`status_${response.status}`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength !== expected) {
      throw new Error(`size_${bytes.byteLength}_expected_${expected}`);
    }
    return bytes;
  });
}

async function patchTusChunk(uploadUrl: string, offset: number, bytes: Uint8Array): Promise<number> {
  const { serverKey } = storageConfig();
  return await retry(`tus_patch_${offset}`, async () => {
    const current = await readTusOffset(uploadUrl);
    if (current !== offset) return current;
    const response = await fetch(uploadUrl, {
      method: "PATCH",
      headers: {
        "Authorization": `Bearer ${serverKey}`,
        "apikey": serverKey,
        "Tus-Resumable": "1.0.0",
        "Upload-Offset": String(offset),
        "Content-Type": "application/offset+octet-stream",
      },
      body: bytes,
    });
    if (response.status !== 204) {
      throw new Error(`status_${response.status}:${(await response.text()).slice(0, 500)}`);
    }
    const next = Number(response.headers.get("upload-offset") || -1);
    if (!Number.isSafeInteger(next) || next < offset || next > offset + bytes.byteLength) {
      throw new Error(`invalid_offset_${next}`);
    }
    return next;
  });
}

async function resumableAssetToStorage(version: string, asset: MirrorAsset): Promise<void> {
  const uploadUrl = await retry("tus_create", () => createTusUpload(version, asset));
  let offset = await readTusOffset(uploadUrl);
  while (offset < asset.size) {
    const bytes = await fetchSourceChunk(asset, offset);
    const next = await patchTusChunk(uploadUrl, offset, bytes);
    if (next === offset) throw new Error(`tus_upload_stalled_at_${offset}`);
    offset = next;
  }
  if (offset !== asset.size) throw new Error(`tus_upload_size_mismatch:${offset}:${asset.size}`);
}

async function streamAssetToStorage(version: string, asset: MirrorAsset): Promise<void> {
  if (asset.size <= STANDARD_UPLOAD_LIMIT) {
    await standardStreamAssetToStorage(version, asset);
    return;
  }
  await resumableAssetToStorage(version, asset);
}

async function ensureMirrorAsset(
  admin: ReturnType<typeof createAdminClient>,
  stable: StableRelease,
  asset: MirrorAsset,
): Promise<boolean> {
  if (await mirrorAssetExists(admin, stable.version, asset)) return true;
  await streamAssetToStorage(stable.version, asset);
  if (!(await mirrorAssetExists(admin, stable.version, asset))) {
    throw new Error(`update_asset_verify_failed:${asset.name}`);
  }
  console.log(`update mirror ready: ${mirrorFolder(stable.version)}/${asset.name}`);
  return true;
}

function mirrorLockPath(version: string): string {
  return `${mirrorFolder(version)}/.package-mirror.lock`;
}

async function claimMirrorLock(admin: ReturnType<typeof createAdminClient>, version: string): Promise<boolean> {
  const folder = mirrorFolder(version);
  const lockName = ".package-mirror.lock";
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).list(folder, {
    limit: 20,
    search: lockName,
  });
  if (error) throw error;
  const existing = data?.find((item: any) => item?.name === lockName);
  if (existing) {
    const createdAt = Date.parse(String(existing.created_at || existing.updated_at || ""));
    if (Number.isFinite(createdAt) && Date.now() - createdAt < MIRROR_LOCK_MAX_AGE_MS) return false;
    await admin.storage.from(UPDATE_BUCKET).remove([mirrorLockPath(version)]);
  }

  const { error: lockError } = await admin.storage.from(UPDATE_BUCKET).upload(
    mirrorLockPath(version),
    new Blob([String(Date.now())], { type: "application/json" }),
    { contentType: "application/json", cacheControl: "60", upsert: false },
  );
  if (!lockError) return true;
  if (/already exists|duplicate/i.test(String(lockError.message || ""))) return false;
  throw lockError;
}

async function releaseMirrorLock(admin: ReturnType<typeof createAdminClient>, version: string) {
  const { error } = await admin.storage.from(UPDATE_BUCKET).remove([mirrorLockPath(version)]);
  if (error) console.error("update mirror lock cleanup failed", error);
}

async function warmUpdatePackages(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  if (!(await claimMirrorLock(admin, stable.version))) return;
  try {
    for (const asset of stable.packageAssets) {
      try {
        await ensureMirrorAsset(admin, stable, asset);
      } catch (error) {
        console.error("update package mirror warmup failed", asset.name, error);
        throw error;
      }
    }
  } finally {
    await releaseMirrorLock(admin, stable.version);
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }
  if (req.method !== "GET") {
    return json(req, { error: "method_not_allowed" }, 405);
  }

  try {
    const admin = createAdminClient();
    const stable = await resolveStableRelease(admin);
    const history = await resolveStableHistory(stable.version).catch((error) => {
      console.error("release history resolution failed", error);
      return [];
    });

    let mirrorFeedReady = false;
    try {
      mirrorFeedReady = await ensureMirrorAsset(admin, stable, stable.feedAsset);
    } catch (error) {
      console.error("update feed mirror warmup failed", error);
    }
    if (mirrorFeedReady) {
      EdgeRuntime.waitUntil(warmUpdatePackages(admin, stable));
    }

    const updateSources = mirrorFeedReady
      ? [mirrorBaseUrl(stable.version), GITHUB_UPDATE_SOURCE]
      : [GITHUB_UPDATE_SOURCE];

    return json(req, {
      channel: "stable",
      version: `v${stable.version}`,
      updateBaseUrl: updateSources[0],
      updateSources,
      mirrorReady: mirrorFeedReady,
      title: stable.title,
      notes: stable.notes,
      publishedAt: stable.publishedAt,
      required: stable.required,
      minSupportedVersion: stable.minSupportedVersion,
      fileSize: stable.fileSize,
      fileSizeBytes: stable.installerSize,
      installerSha256: stable.installerSha256,
      history,
    });
  } catch (error) {
    console.error("public release metadata resolution failed", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
