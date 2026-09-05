import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const UPDATE_BUCKET = "listing-studio-updates";
const DOWNLOAD_BUCKET = "listing-studio-downloads";
const UPDATE_CHANNEL = "win-x64-stable";
const UPDATE_FEED_NAME = `releases.${UPDATE_CHANNEL}.json`;
const STABLE_CACHE_PATH = "stable/latest.json";
const HISTORY_CACHE_PATH = "stable/history.json";
const CHUNK_BYTES = 32 * 1024 * 1024;
const MAX_CHUNKS_PER_CALL = 3;
const CHUNK_MANIFEST_SCHEMA = 1;
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_DIGEST_RE = /^sha256:([0-9a-f]{64})$/i;

type ReleaseAsset = {
  id: number;
  name: string;
  size: number;
  sha256: string;
  browserUrl: string;
  contentType: string;
};

type ChunkPart = { name: string; offset: number; size: number };

type StableCache = {
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
  feedAsset: { name: string; sourceUrl: string; size: number; contentType: string; sha256: string };
  packageAssets: Array<{ name: string; sourceUrl: string; size: number; contentType: string; sha256: string }>;
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function getServerSecretKey(): string {
  const bundle = Deno.env.get("SUPABASE_SECRET_KEYS") || "";
  if (bundle) {
    try {
      const parsed = JSON.parse(bundle);
      const value = String(parsed?.default || "").trim();
      if (value) return value;
    } catch {}
  }
  return String(Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
}

function createAdminClient() {
  const url = String(Deno.env.get("SUPABASE_URL") || "").trim().replace(/\/$/, "");
  const key = getServerSecretKey();
  if (!url || !key) throw new Error("server_storage_config_missing");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

function githubHeaders(token: string, accept = "application/vnd.github+json"): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    Accept: accept,
    "User-Agent": "Listing-Studio-Release-Mirror",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function normalizeVersion(value: unknown): string {
  const raw = String(value ?? "").trim();
  return raw.startsWith("v") ? raw.slice(1) : raw;
}

function formatBytes(bytes: number): string {
  const mib = bytes / 1024 / 1024;
  return `${mib >= 100 ? mib.toFixed(0) : mib.toFixed(1)} MB`;
}

function githubDownloadUrl(version: string, name: string): string {
  return `https://github.com/${REPOSITORY}/releases/download/v${version}/${name}`;
}

function parseAsset(release: any, version: string, name: string, contentType: string, requireDigest = true): ReleaseAsset | null {
  const raw = Array.isArray(release?.assets) ? release.assets.find((item: any) => item?.name === name) : null;
  const id = Number(raw?.id || 0);
  const size = Number(raw?.size || 0);
  const digest = String(raw?.digest || "").trim().toLowerCase();
  const match = digest.match(SHA256_DIGEST_RE);
  const expectedUrl = githubDownloadUrl(version, name);
  if (
    !Number.isSafeInteger(id) || id <= 0 ||
    !Number.isSafeInteger(size) || size <= 0 ||
    String(raw?.browser_download_url || "") !== expectedUrl ||
    (requireDigest && !match)
  ) return null;
  return {
    id,
    name,
    size,
    sha256: match?.[1]?.toLowerCase() || "",
    browserUrl: expectedUrl,
    contentType,
  };
}

async function getRelease(token: string, tag: string) {
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/releases/tags/${encodeURIComponent(tag)}`, {
    headers: githubHeaders(token),
    cache: "no-store",
  });
  if (response.status === 401 || response.status === 403 || response.status === 404) {
    throw new Error("github_token_cannot_read_release");
  }
  if (!response.ok) throw new Error(`github_release_${response.status}`);
  const release = await response.json();
  if (!release || release.draft || release.prerelease || String(release.tag_name || "") !== tag) {
    throw new Error("release_not_published_stable");
  }
  return release;
}

async function fetchAssetBytes(token: string, asset: ReleaseAsset): Promise<Uint8Array> {
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/releases/assets/${asset.id}`, {
    headers: githubHeaders(token, "application/octet-stream"),
    cache: "no-store",
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`asset_fetch_${response.status}:${asset.name}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== asset.size) throw new Error(`asset_size_mismatch:${asset.name}`);
  return bytes;
}

async function fetchAssetRange(token: string, asset: ReleaseAsset, part: ChunkPart): Promise<Uint8Array> {
  const end = part.offset + part.size - 1;
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/releases/assets/${asset.id}`, {
    headers: {
      ...githubHeaders(token, "application/octet-stream"),
      Range: `bytes=${part.offset}-${end}`,
    },
    cache: "no-store",
    redirect: "follow",
  });
  if (response.status !== 206) {
    try { await response.body?.cancel(); } catch {}
    throw new Error(`asset_range_${response.status}:${asset.name}:${part.name}`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== part.size) throw new Error(`asset_range_size:${asset.name}:${part.name}`);
  return bytes;
}

async function listedObject(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  folder: string,
  name: string,
) {
  const { data, error } = await admin.storage.from(bucket).list(folder, { limit: 100, search: name });
  if (error) throw error;
  return data?.find((item: any) => item?.name === name) || null;
}

async function objectReady(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  folder: string,
  name: string,
  expectedSize: number,
): Promise<boolean> {
  const hit = await listedObject(admin, bucket, folder, name);
  if (!hit) return false;
  const size = Number(hit?.metadata?.size ?? hit?.metadata?.contentLength ?? 0);
  return size === expectedSize;
}

async function removeIfWrongSize(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  folder: string,
  name: string,
  expectedSize: number,
) {
  const hit = await listedObject(admin, bucket, folder, name);
  if (!hit) return;
  const size = Number(hit?.metadata?.size ?? hit?.metadata?.contentLength ?? 0);
  if (size === expectedSize) return;
  const { error } = await admin.storage.from(bucket).remove([`${folder}/${name}`]);
  if (error) throw error;
}

async function uploadBytes(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  path: string,
  bytes: Uint8Array,
  contentType: string,
) {
  const { error } = await admin.storage.from(bucket).upload(path, bytes, {
    contentType,
    cacheControl: "31536000",
    upsert: false,
  });
  if (error && !/already exists|duplicate/i.test(String(error.message || ""))) throw error;
}

function updateFolder(version: string): string {
  return `stable/v${version}`;
}

function chunkFolder(baseFolder: string, asset: ReleaseAsset): string {
  return `${baseFolder}/chunks/${asset.name}`;
}

function installerFolder(version: string, asset: ReleaseAsset): string {
  return `stable/v${version}/${asset.name}`;
}

function chunkPlan(asset: ReleaseAsset): ChunkPart[] {
  const parts: ChunkPart[] = [];
  for (let offset = 0, index = 0; offset < asset.size; index += 1) {
    const size = Math.min(CHUNK_BYTES, asset.size - offset);
    parts.push({ name: `part-${String(index).padStart(3, "0")}`, offset, size });
    offset += size;
  }
  return parts;
}

async function writeManifest(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  folder: string,
  version: string,
  asset: ReleaseAsset,
) {
  const manifest = {
    schema_version: CHUNK_MANIFEST_SCHEMA,
    version: `v${version}`,
    file_name: asset.name,
    size: asset.size,
    sha256: asset.sha256,
    chunk_size: CHUNK_BYTES,
    chunks: chunkPlan(asset),
  };
  const { error } = await admin.storage.from(bucket).upload(
    `${folder}/manifest.json`,
    new Blob([JSON.stringify(manifest)], { type: "application/json" }),
    { contentType: "application/json", cacheControl: "31536000", upsert: true },
  );
  if (error) throw error;
}

async function manifestReady(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  folder: string,
  version: string,
  asset: ReleaseAsset,
): Promise<boolean> {
  const { data, error } = await admin.storage.from(bucket).download(`${folder}/manifest.json`);
  if (error || !data) return false;
  try {
    const manifest = JSON.parse(await data.text());
    const planned = chunkPlan(asset);
    if (
      manifest?.schema_version !== CHUNK_MANIFEST_SCHEMA ||
      manifest?.version !== `v${version}` ||
      manifest?.file_name !== asset.name ||
      Number(manifest?.size || 0) !== asset.size ||
      String(manifest?.sha256 || "").toLowerCase() !== asset.sha256 ||
      !Array.isArray(manifest?.chunks) || manifest.chunks.length !== planned.length
    ) return false;
    for (let i = 0; i < planned.length; i += 1) {
      const actual = manifest.chunks[i];
      const expected = planned[i];
      if (actual?.name !== expected.name || Number(actual?.offset) !== expected.offset || Number(actual?.size) !== expected.size) return false;
      if (!(await objectReady(admin, bucket, folder, expected.name, expected.size))) return false;
    }
    return true;
  } catch {
    return false;
  }
}

async function ensureDirectAsset(
  admin: ReturnType<typeof createAdminClient>,
  token: string,
  bucket: string,
  folder: string,
  asset: ReleaseAsset,
) {
  if (await objectReady(admin, bucket, folder, asset.name, asset.size)) return true;
  await removeIfWrongSize(admin, bucket, folder, asset.name, asset.size);
  const bytes = await fetchAssetBytes(token, asset);
  await uploadBytes(admin, bucket, `${folder}/${asset.name}`, bytes, asset.contentType);
  return await objectReady(admin, bucket, folder, asset.name, asset.size);
}

async function ensureChunkedAsset(
  admin: ReturnType<typeof createAdminClient>,
  token: string,
  bucket: string,
  folder: string,
  version: string,
  asset: ReleaseAsset,
  budget: { remaining: number },
): Promise<boolean> {
  if (await manifestReady(admin, bucket, folder, version, asset)) return true;
  const plan = chunkPlan(asset);
  for (const part of plan) {
    if (await objectReady(admin, bucket, folder, part.name, part.size)) continue;
    if (budget.remaining <= 0) return false;
    await removeIfWrongSize(admin, bucket, folder, part.name, part.size);
    const bytes = await fetchAssetRange(token, asset, part);
    await uploadBytes(admin, bucket, `${folder}/${part.name}`, bytes, "application/octet-stream");
    if (!(await objectReady(admin, bucket, folder, part.name, part.size))) {
      throw new Error(`chunk_verify_failed:${asset.name}:${part.name}`);
    }
    budget.remaining -= 1;
  }
  await writeManifest(admin, bucket, folder, version, asset);
  return await manifestReady(admin, bucket, folder, version, asset);
}

function toMirrorAsset(asset: ReleaseAsset) {
  return {
    name: asset.name,
    sourceUrl: asset.browserUrl,
    size: asset.size,
    contentType: asset.contentType,
    sha256: asset.sha256,
  };
}

function buildStableCache(release: any, version: string, installer: ReleaseAsset, feed: ReleaseAsset, packages: ReleaseAsset[]): StableCache {
  return {
    version,
    title: String(release?.name || `Listing Studio ${version}`).trim() || `Listing Studio ${version}`,
    notes: String(release?.body || "").trim(),
    publishedAt: String(release?.published_at || release?.created_at || "").trim(),
    required: false,
    minSupportedVersion: "",
    installerName: installer.name,
    installerUrl: installer.browserUrl,
    installerSha256: installer.sha256,
    installerSize: installer.size,
    fileSize: formatBytes(installer.size),
    feedAsset: toMirrorAsset(feed),
    packageAssets: packages.map(toMirrorAsset),
  };
}

async function persistJson(
  admin: ReturnType<typeof createAdminClient>,
  bucket: string,
  path: string,
  body: unknown,
  cacheControl = "30",
) {
  const { error } = await admin.storage.from(bucket).upload(
    path,
    new Blob([JSON.stringify(body)], { type: "application/json" }),
    { contentType: "application/json", cacheControl, upsert: true },
  );
  if (error) throw error;
}

async function refreshHistory(admin: ReturnType<typeof createAdminClient>, token: string) {
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/releases?per_page=20`, {
    headers: githubHeaders(token),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`history_fetch_${response.status}`);
  const releases = await response.json();
  if (!Array.isArray(releases)) throw new Error("history_invalid");
  const history = releases.flatMap((release: any) => {
    if (!release || release.draft || release.prerelease) return [];
    const version = normalizeVersion(release.tag_name);
    if (!VERSION_RE.test(version) || String(release.tag_name || "") !== `v${version}`) return [];
    const installer = parseAsset(release, version, `EcommerceAgent-Setup-${version}.exe`, "application/octet-stream", true);
    if (!installer) return [];
    return [{
      version: `v${version}`,
      title: String(release.name || `Listing Studio ${version}`).trim() || `Listing Studio ${version}`,
      notes: String(release.body || "").trim(),
      publishedAt: String(release.published_at || release.created_at || "").trim(),
      fileSize: formatBytes(installer.size),
      fileSizeBytes: installer.size,
      installerSha256: installer.sha256,
    }];
  }).slice(0, 12);
  await persistJson(admin, UPDATE_BUCKET, HISTORY_CACHE_PATH, history, "60");
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const authorization = String(req.headers.get("authorization") || "");
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7).trim() : "";
  if (!token) return json({ error: "missing_github_token" }, 401);

  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { return json({ error: "invalid_json" }, 400); }
  const tag = String(body.tag || "").trim();
  const version = normalizeVersion(tag);
  if (!VERSION_RE.test(version) || tag !== `v${version}`) return json({ error: "invalid_tag" }, 400);

  try {
    const release = await getRelease(token, tag);
    const installer = parseAsset(release, version, `EcommerceAgent-Setup-${version}.exe`, "application/octet-stream", true);
    const feed = parseAsset(release, version, UPDATE_FEED_NAME, "application/json", false);
    const full = parseAsset(release, version, `Smirel.ListingStudio-${version}-${UPDATE_CHANNEL}-full.nupkg`, "application/octet-stream", true);
    const delta = parseAsset(release, version, `Smirel.ListingStudio-${version}-${UPDATE_CHANNEL}-delta.nupkg`, "application/octet-stream", true);
    if (!installer || !feed || !full) throw new Error("required_release_assets_missing");

    const admin = createAdminClient();
    const updateBase = updateFolder(version);
    if (!(await ensureDirectAsset(admin, token, UPDATE_BUCKET, updateBase, feed))) throw new Error("feed_mirror_failed");
    if (delta && !(await ensureDirectAsset(admin, token, UPDATE_BUCKET, updateBase, delta))) throw new Error("delta_mirror_failed");

    const budget = { remaining: MAX_CHUNKS_PER_CALL };
    const fullReady = await ensureChunkedAsset(
      admin, token, UPDATE_BUCKET, chunkFolder(updateBase, full), version, full, budget,
    );
    const installerBase = installerFolder(version, installer);
    const installerReady = await ensureChunkedAsset(
      admin, token, DOWNLOAD_BUCKET, installerBase, version, installer, budget,
    );

    const ready = fullReady && installerReady;
    if (ready) {
      const packages = delta ? [delta, full] : [full];
      await persistJson(admin, UPDATE_BUCKET, STABLE_CACHE_PATH, buildStableCache(release, version, installer, feed, packages));
      await refreshHistory(admin, token);
    }

    return json({
      version: tag,
      ready,
      updateMirrorReady: fullReady,
      installerMirrorReady: installerReady,
      chunksRemainingThisCall: budget.remaining,
    });
  } catch (error) {
    console.error("private Stable mirror warmup failed", error);
    const message = error instanceof Error ? error.message : "mirror_failed";
    return json({ error: message }, message === "github_token_cannot_read_release" ? 403 : 503);
  }
});
