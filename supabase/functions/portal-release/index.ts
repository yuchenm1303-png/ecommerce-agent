import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const UPDATE_BUCKET = "listing-studio-updates";
const UPDATE_CHANNEL = "win-x64-stable";
const STABLE_CACHE_PATH = "stable/latest.json";
const HISTORY_CACHE_PATH = "stable/history.json";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_HEX_RE = /^[0-9a-f]{64}$/i;
const MIRROR_CHUNK_BYTES = 32 * 1024 * 1024;
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

type HistoryItem = {
  version: string;
  title: string;
  notes: string;
  publishedAt: string;
  fileSize: string;
  fileSizeBytes: number;
  installerSha256: string;
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

function mirrorFolder(version: string): string {
  return `stable/v${version}`;
}

function mirrorBaseUrl(version: string): string {
  const { supabaseUrl } = storageConfig();
  return `${supabaseUrl}/storage/v1/object/public/${UPDATE_BUCKET}/${mirrorFolder(version)}`;
}

function validateAsset(value: any): MirrorAsset | null {
  const name = String(value?.name || "").trim();
  const sourceUrl = String(value?.sourceUrl || "").trim();
  const size = Number(value?.size || 0);
  const contentType = String(value?.contentType || "application/octet-stream").trim();
  const sha256 = String(value?.sha256 || "").trim().toLowerCase();
  if (
    !name || name !== name.split(/[\\/]/).pop() ||
    !Number.isSafeInteger(size) || size <= 0 ||
    (sha256 && !SHA256_HEX_RE.test(sha256))
  ) return null;
  return { name, sourceUrl, size, contentType, sha256 };
}

function validateStableCache(value: any): StableRelease {
  const version = String(value?.version || "").trim();
  const installerName = String(value?.installerName || "").trim();
  const installerSha256 = String(value?.installerSha256 || "").trim().toLowerCase();
  const installerSize = Number(value?.installerSize || 0);
  const feedAsset = validateAsset(value?.feedAsset);
  const packageAssets = Array.isArray(value?.packageAssets)
    ? value.packageAssets.map(validateAsset).filter(Boolean) as MirrorAsset[]
    : [];

  if (
    !VERSION_RE.test(version) ||
    installerName !== `EcommerceAgent-Setup-${version}.exe` ||
    !SHA256_HEX_RE.test(installerSha256) ||
    !Number.isSafeInteger(installerSize) || installerSize <= 0 ||
    !feedAsset || feedAsset.name !== `releases.${UPDATE_CHANNEL}.json` ||
    packageAssets.length === 0
  ) {
    throw new Error("stable_cache_invalid");
  }

  return {
    version,
    title: String(value?.title || `Listing Studio ${version}`).trim() || `Listing Studio ${version}`,
    notes: String(value?.notes || "").trim(),
    publishedAt: String(value?.publishedAt || "").trim(),
    required: Boolean(value?.required),
    minSupportedVersion: String(value?.minSupportedVersion || "").trim(),
    installerName,
    installerUrl: String(value?.installerUrl || "").trim(),
    installerSha256,
    installerSize,
    fileSize: String(value?.fileSize || "—").trim() || "—",
    feedAsset,
    packageAssets,
  };
}

async function loadJson(admin: ReturnType<typeof createAdminClient>, path: string) {
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).download(path);
  if (error || !data) throw error || new Error(`storage_object_missing:${path}`);
  return JSON.parse(await data.text());
}

async function loadStableCache(admin: ReturnType<typeof createAdminClient>): Promise<StableRelease> {
  return validateStableCache(await loadJson(admin, STABLE_CACHE_PATH));
}

async function loadHistoryCache(
  admin: ReturnType<typeof createAdminClient>,
  currentVersion: string,
): Promise<HistoryItem[]> {
  try {
    const raw = await loadJson(admin, HISTORY_CACHE_PATH);
    if (!Array.isArray(raw)) return [];
    return raw.flatMap((item: any) => {
      const version = String(item?.version || "").trim();
      const bare = version.startsWith("v") ? version.slice(1) : version;
      const fileSizeBytes = Number(item?.fileSizeBytes || 0);
      const sha256 = String(item?.installerSha256 || "").trim().toLowerCase();
      if (
        !VERSION_RE.test(bare) || bare === currentVersion ||
        !Number.isSafeInteger(fileSizeBytes) || fileSizeBytes <= 0 ||
        !SHA256_HEX_RE.test(sha256)
      ) return [];
      return [{
        version: `v${bare}`,
        title: String(item?.title || `Listing Studio ${bare}`).trim() || `Listing Studio ${bare}`,
        notes: String(item?.notes || "").trim(),
        publishedAt: String(item?.publishedAt || "").trim(),
        fileSize: String(item?.fileSize || "—").trim() || "—",
        fileSizeBytes,
        installerSha256: sha256,
      }];
    }).slice(0, 12);
  } catch (error) {
    console.error("Stable history cache unavailable", error);
    return [];
  }
}

async function objectReady(
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

function chunkFolder(version: string, asset: MirrorAsset): string {
  return `${mirrorFolder(version)}/chunks/${asset.name}`;
}

async function chunkManifestReady(
  admin: ReturnType<typeof createAdminClient>,
  stable: StableRelease,
  asset: MirrorAsset,
): Promise<boolean> {
  const folder = chunkFolder(stable.version, asset);
  const { data, error } = await admin.storage.from(UPDATE_BUCKET).download(`${folder}/manifest.json`);
  if (error || !data) return false;
  try {
    const manifest = JSON.parse(await data.text());
    if (
      manifest?.schema_version !== CHUNK_MANIFEST_SCHEMA ||
      manifest?.file_name !== asset.name ||
      Number(manifest?.size || 0) !== asset.size ||
      String(manifest?.sha256 || "").trim().toLowerCase() !== asset.sha256 ||
      !Array.isArray(manifest?.chunks) || manifest.chunks.length === 0
    ) return false;

    let expectedOffset = 0;
    for (let index = 0; index < manifest.chunks.length; index += 1) {
      const part = manifest.chunks[index];
      const name = String(part?.name || "");
      const offset = Number(part?.offset);
      const size = Number(part?.size);
      if (
        name !== `part-${String(index).padStart(3, "0")}` ||
        !Number.isSafeInteger(offset) || offset !== expectedOffset ||
        !Number.isSafeInteger(size) || size <= 0 ||
        !(await objectReady(admin, folder, name, size))
      ) return false;
      expectedOffset += size;
    }
    return expectedOffset === asset.size;
  } catch {
    return false;
  }
}

async function assetReady(
  admin: ReturnType<typeof createAdminClient>,
  stable: StableRelease,
  asset: MirrorAsset,
): Promise<boolean> {
  if (asset.size <= MIRROR_CHUNK_BYTES) {
    return await objectReady(admin, mirrorFolder(stable.version), asset.name, asset.size);
  }
  return await chunkManifestReady(admin, stable, asset);
}

async function releaseMirrorReady(admin: ReturnType<typeof createAdminClient>, stable: StableRelease) {
  if (!(await objectReady(admin, mirrorFolder(stable.version), stable.feedAsset.name, stable.feedAsset.size))) {
    return false;
  }
  for (const asset of stable.packageAssets) {
    if (!(await assetReady(admin, stable, asset))) return false;
  }
  return true;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "GET") return json(req, { error: "method_not_allowed" }, 405);

  try {
    const admin = createAdminClient();
    const stable = await loadStableCache(admin);
    if (!(await releaseMirrorReady(admin, stable))) {
      throw new Error(`stable_mirror_incomplete:v${stable.version}`);
    }

    const history = await loadHistoryCache(admin, stable.version);
    const baseUrl = mirrorBaseUrl(stable.version);

    return json(req, {
      channel: "stable",
      version: `v${stable.version}`,
      updateBaseUrl: baseUrl,
      updateSources: [baseUrl],
      mirrorReady: true,
      packageMirrorReady: true,
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
    console.error("mirrored Stable metadata unavailable", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
