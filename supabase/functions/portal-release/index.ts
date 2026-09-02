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
const MIRROR_CHUNK_BYTES = 32 * 1024 * 1024;
const MAX_CHUNKS_PER_WARM = 3;
const MIRROR_LOCK_MAX_AGE_MS = 15 * 60 * 1000;
const CHUNK_MANIFEST_SCHEMA = 1;
const ALLOWED_ORIGINS = new Set([
  "https://smirel.com",
  "https://www.smirel.com",
]);

type MirrorAsset = {
  name: string;
  sourceUrl: string;
  size: number;
  contentType: string;
  sha256: string;
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

type ChunkPart = {
  name: string;
  offset: number;
  size: number;
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
  return { supabaseUrl, serverKey };
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
  const digest = String(asset?.digest || "").trim().toLowerCase();
  const digestMatch = digest.match(SHA256_DIGEST_RE);
  if (
    String(asset?.browser_download_url || "") !== expectedUrl ||
    !Number.isSafeInteger(size) ||
    size <= 0
  ) {
    return null;
  }
  return {
    name,
    sourceUrl: expectedUrl,
    size,
    contentType,
    sha256: digestMatch?.[1]?.toLowerCase() || "",
  };
}

function parseStableRelease(release: any): StableRelease | null {
  if (!release || release.draft || release.prerelease || !Array.isArray(release.assets)) return null;

  const version = normalizeVersion(release.tag_name);
  if (!VERSION_RE.test(version) || String(release.tag_name || "") !== `v${version}`) return null;

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
  ) return null;

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
    const response = await fetch(String(legacyManifestAsset.browser_download_url), {
      headers: { "User-Agent": "Listing-Studio-Release-Metadata" },
      cache: "no-store",
      redirect: "follow",
    });
    if (!response.ok) return stable;
    const manifest = await response.json();
    if (
      manifest?.schema_version !== 1 ||
      manifest?.channel !== "stable" ||
      normalizeVersion(manifest?.version) !== stable.version
    ) return stable;
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
  const response = await fetch(LATEST_RELEASE_API, {
    headers: githubHeaders("Listing-Studio-Release-Metadata"),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`release_api_${response.status}`);
  const release = await response.json();
  const parsed = parseStableRelease(release);
  if (!parsed) throw new Error("invalid_latest_release");
  return await applyLegacyMetadata(parsed, release);
}

async function persistStableCache(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  const { error } = await admin.storage.from(UPDATE_BUCKET).upload(
    STABLE_CACHE_PATH,
    new Blob([JSON.stringify(stable)], { type: "application/json" }),
    { contentType: "application/json", cacheControl: "30", upsert: true },
  );
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
  } catch (error) {
    console.error("GitHub Stable control-plane lookup failed; using last validated cache", error);
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

async function objectExists(
  admin: ReturnType<typeof createAdminClient>,
  folder: string,
  name: string,
  expectedSize: number,
): Promise<boolean> {
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).list(folder, { limit: 100, search: name });
  if (error) throw error;
  const hit = data?.find((item: any) => item?.name === name);
  if (!hit) return false;
  const size = Number(hit?.metadata?.size ?? hit?.metadata?.contentLength ?? 0);
  return size === expectedSize;
}

async function directAssetExists(admin: ReturnType<typeof createAdminClient>, version: string, asset: MirrorAsset) {
  return await objectExists(admin, mirrorFolder(version), asset.name, asset.size);
}

async function uploadBytes(
  admin: ReturnType<typeof createAdminClient>,
  path: string,
  bytes: Uint8Array,
  contentType: string,
) {
  const { error } = await admin.storage.from(UPDATE_BUCKET).upload(path, bytes, {
    contentType,
    cacheControl: "31536000",
    upsert: false,
  });
  if (error && !/already exists|duplicate/i.test(String(error.message || ""))) throw error;
}

async function fetchWholeAsset(asset: MirrorAsset): Promise<Uint8Array> {
  const response = await fetch(asset.sourceUrl, {
    headers: { "User-Agent": "Listing-Studio-Update-Mirror" },
    cache: "no-store",
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`update_asset_fetch_${response.status}:${asset.name}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== asset.size) throw new Error(`update_asset_size:${asset.name}:${bytes.byteLength}:${asset.size}`);
  return bytes;
}

async function ensureDirectAsset(admin: ReturnType<typeof createAdminClient>, stable: StableRelease, asset: MirrorAsset) {
  if (await directAssetExists(admin, stable.version, asset)) return true;
  const bytes = await fetchWholeAsset(asset);
  await uploadBytes(admin, `${mirrorFolder(stable.version)}/${asset.name}`, bytes, asset.contentType);
  if (!(await directAssetExists(admin, stable.version, asset))) throw new Error(`update_asset_verify_failed:${asset.name}`);
  console.log(`update mirror ready: ${mirrorFolder(stable.version)}/${asset.name}`);
  return true;
}

function chunkFolder(version: string, asset: MirrorAsset): string {
  return `${mirrorFolder(version)}/chunks/${asset.name}`;
}

function chunkPlan(asset: MirrorAsset): ChunkPart[] {
  const parts: ChunkPart[] = [];
  for (let offset = 0, index = 0; offset < asset.size; index += 1) {
    const size = Math.min(MIRROR_CHUNK_BYTES, asset.size - offset);
    parts.push({ name: `part-${String(index).padStart(3, "0")}`, offset, size });
    offset += size;
  }
  return parts;
}

async function fetchRange(asset: MirrorAsset, part: ChunkPart): Promise<Uint8Array> {
  const end = part.offset + part.size - 1;
  const response = await fetch(asset.sourceUrl, {
    headers: {
      "User-Agent": "Listing-Studio-Update-Mirror",
      "Range": `bytes=${part.offset}-${end}`,
    },
    cache: "no-store",
    redirect: "follow",
  });
  if (response.status !== 206) throw new Error(`update_asset_range_${response.status}:${asset.name}:${part.name}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== part.size) throw new Error(`update_chunk_size:${asset.name}:${part.name}:${bytes.byteLength}:${part.size}`);
  return bytes;
}

async function chunkManifestReady(admin: ReturnType<typeof createAdminClient>, stable: StableRelease, asset: MirrorAsset) {
  const folder = chunkFolder(stable.version, asset);
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).download(`${folder}/manifest.json`);
  if (error || !data) return false;
  try {
    const manifest = JSON.parse(await data.text());
    if (
      manifest?.schema_version !== CHUNK_MANIFEST_SCHEMA ||
      manifest?.file_name !== asset.name ||
      Number(manifest?.size || 0) !== asset.size ||
      !Array.isArray(manifest?.chunks)
    ) return false;
    const planned = chunkPlan(asset);
    if (manifest.chunks.length !== planned.length) return false;
    for (let i = 0; i < planned.length; i += 1) {
      const actual = manifest.chunks[i];
      const expected = planned[i];
      if (actual?.name !== expected.name || Number(actual?.offset) !== expected.offset || Number(actual?.size) !== expected.size) return false;
    }
    return true;
  } catch {
    return false;
  }
}

async function writeChunkManifest(admin: ReturnType<typeof createAdminClient>, stable: StableRelease, asset: MirrorAsset) {
  const manifest = {
    schema_version: CHUNK_MANIFEST_SCHEMA,
    file_name: asset.name,
    size: asset.size,
    sha256: asset.sha256,
    chunk_size: MIRROR_CHUNK_BYTES,
    chunks: chunkPlan(asset),
  };
  const path = `${chunkFolder(stable.version, asset)}/manifest.json`;
  const { error } = await admin.storage.from(UPDATE_BUCKET).upload(
    path,
    new Blob([JSON.stringify(manifest)], { type: "application/json" }),
    { contentType: "application/json", cacheControl: "31536000", upsert: true },
  );
  if (error) throw error;
}

async function ensureChunkedAsset(admin: ReturnType<typeof createAdminClient>, stable: StableRelease, asset: MirrorAsset) {
  if (await chunkManifestReady(admin, stable, asset)) return true;
  const folder = chunkFolder(stable.version, asset);
  const plan = chunkPlan(asset);
  let uploaded = 0;
  for (const part of plan) {
    if (await objectExists(admin, folder, part.name, part.size)) continue;
    if (uploaded >= MAX_CHUNKS_PER_WARM) return false;
    const bytes = await fetchRange(asset, part);
    await uploadBytes(admin, `${folder}/${part.name}`, bytes, "application/octet-stream");
    if (!(await objectExists(admin, folder, part.name, part.size))) throw new Error(`update_chunk_verify_failed:${asset.name}:${part.name}`);
    uploaded += 1;
  }
  for (const part of plan) {
    if (!(await objectExists(admin, folder, part.name, part.size))) return false;
  }
  await writeChunkManifest(admin, stable, asset);
  if (!(await chunkManifestReady(admin, stable, asset))) throw new Error(`update_chunk_manifest_verify_failed:${asset.name}`);
  console.log(`chunked update mirror ready: ${asset.name} (${plan.length} parts)`);
  return true;
}

function mirrorLockPath(version: string): string {
  return `${mirrorFolder(version)}/.package-mirror.lock`;
}

async function claimMirrorLock(admin: ReturnType<typeof createAdminClient>, version: string): Promise<boolean> {
  const folder = mirrorFolder(version);
  const lockName = ".package-mirror.lock";
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).list(folder, { limit: 20, search: lockName });
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

async function packageAssetReady(admin: ReturnType<typeof createAdminClient>, stable: StableRelease, asset: MirrorAsset) {
  if (asset.size <= MIRROR_CHUNK_BYTES) return await directAssetExists(admin, stable.version, asset);
  return await chunkManifestReady(admin, stable, asset);
}

async function packageMirrorReady(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  for (const asset of stable.packageAssets) {
    if (!(await packageAssetReady(admin, stable, asset))) return false;
  }
  return true;
}

async function warmUpdatePackages(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  if (!(await claimMirrorLock(admin, stable.version))) return;
  try {
    for (const asset of stable.packageAssets) {
      if (asset.size <= MIRROR_CHUNK_BYTES) {
        await ensureDirectAsset(admin, stable, asset);
      } else {
        await ensureChunkedAsset(admin, stable, asset);
      }
    }
  } catch (error) {
    console.error("update package mirror warmup failed", error);
  } finally {
    await releaseMirrorLock(admin, stable.version);
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "GET") return json(req, { error: "method_not_allowed" }, 405);

  try {
    const admin = createAdminClient();
    const stable = await resolveStableRelease(admin);
    const history = await resolveStableHistory(stable.version).catch((error) => {
      console.error("release history resolution failed", error);
      return [];
    });

    let mirrorFeedReady = false;
    try {
      mirrorFeedReady = await ensureDirectAsset(admin, stable, stable.feedAsset);
    } catch (error) {
      console.error("update feed mirror warmup failed", error);
    }
    const packagesReady = mirrorFeedReady
      ? await packageMirrorReady(admin, stable).catch(() => false)
      : false;
    if (mirrorFeedReady && !packagesReady) EdgeRuntime.waitUntil(warmUpdatePackages(admin, stable));

    const updateSources = mirrorFeedReady
      ? [mirrorBaseUrl(stable.version), GITHUB_UPDATE_SOURCE]
      : [GITHUB_UPDATE_SOURCE];

    return json(req, {
      channel: "stable",
      version: `v${stable.version}`,
      updateBaseUrl: updateSources[0],
      updateSources,
      mirrorReady: mirrorFeedReady,
      packageMirrorReady: packagesReady,
      chunkBytes: MIRROR_CHUNK_BYTES,
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
