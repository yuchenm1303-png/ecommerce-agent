import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const UPDATE_BASE_URL = `https://github.com/${REPOSITORY}`;
const LATEST_RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const RELEASE_HISTORY_API = `https://api.github.com/repos/${REPOSITORY}/releases?per_page=20`;
const RELEASE_BUCKET = "listing-studio-releases";
const LEGACY_MANIFEST_ASSET = "update.json";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_DIGEST_RE = /^sha256:([0-9a-f]{64})$/i;
const HISTORY_LIMIT = 12;
const ALLOWED_ORIGINS = new Set([
  "https://smirel.com",
  "https://www.smirel.com",
]);

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

function createAdminClient() {
  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const serverKey = getServerSecretKey();
  if (!supabaseUrl || !serverKey) throw new Error("server_storage_config_missing");
  return createClient(supabaseUrl, serverKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function parseStableRelease(release: any) {
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
  const expectedUrl = `https://github.com/${REPOSITORY}/releases/download/v${version}/${installerName}`;
  if (
    String(installerAsset?.browser_download_url || "") !== expectedUrl ||
    !Number.isSafeInteger(installerSize) ||
    installerSize <= 0 ||
    !digestMatch
  ) {
    return null;
  }

  return {
    version,
    title: String(release.name || `Listing Studio ${version}`).trim() || `Listing Studio ${version}`,
    notes: String(release.body || "").trim(),
    publishedAt: String(release.published_at || release.created_at || "").trim(),
    required: false,
    minSupportedVersion: "",
    installerName,
    installerUrl: expectedUrl,
    installerSha256: digestMatch[1].toLowerCase(),
    installerSize,
    fileSize: formatBytes(installerSize),
  };
}

async function applyLegacyMetadata(stable: NonNullable<ReturnType<typeof parseStableRelease>>, release: any) {
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

async function resolveStableRelease() {
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
    .filter((item): item is NonNullable<ReturnType<typeof parseStableRelease>> => Boolean(item))
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

async function privateInstallerExists(
  admin: ReturnType<typeof createAdminClient>,
  stable: Awaited<ReturnType<typeof resolveStableRelease>>,
) {
  const folder = `stable/v${stable.version}`;
  const { data, error } = await admin.storage.from(RELEASE_BUCKET).list(folder, {
    limit: 100,
    search: stable.installerName,
  });
  if (error) throw error;
  const hit = data?.find((item: any) => item?.name === stable.installerName);
  if (!hit) return false;
  const size = Number(hit?.metadata?.size ?? hit?.metadata?.contentLength ?? 0);
  return size <= 0 || size === stable.installerSize;
}

async function warmPrivateMirror(stable: Awaited<ReturnType<typeof resolveStableRelease>>) {
  try {
    const admin = createAdminClient();
    if (await privateInstallerExists(admin, stable)) return;

    const source = await fetch(stable.installerUrl, {
      headers: { "User-Agent": "Listing-Studio-Release-Mirror" },
      cache: "no-store",
      redirect: "follow",
    });
    if (!source.ok || !source.body) throw new Error(`installer_source_fetch_${source.status}`);

    const contentLength = Number(source.headers.get("content-length") || 0);
    if (contentLength > 0 && contentLength !== stable.installerSize) {
      try { await source.body.cancel(); } catch {}
      throw new Error("installer_source_size_mismatch");
    }

    const path = `stable/v${stable.version}/${stable.installerName}`;
    const { error } = await admin.storage.from(RELEASE_BUCKET).upload(path, source.body, {
      contentType: "application/octet-stream",
      cacheControl: "31536000",
      upsert: false,
    });
    if (error && !/already exists|duplicate/i.test(String(error.message || ""))) throw error;

    if (!(await privateInstallerExists(admin, stable))) throw new Error("private_release_verify_failed");
    console.log(`private release mirror ready: ${path}`);
  } catch (error) {
    console.error("private release warmup failed", error);
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
    const stable = await resolveStableRelease();
    const history = await resolveStableHistory(stable.version).catch((error) => {
      console.error("release history resolution failed", error);
      return [];
    });
    EdgeRuntime.waitUntil(warmPrivateMirror(stable));

    return json(req, {
      channel: "stable",
      version: `v${stable.version}`,
      updateBaseUrl: UPDATE_BASE_URL,
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
