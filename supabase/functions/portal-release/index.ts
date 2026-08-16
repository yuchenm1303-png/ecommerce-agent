import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const RELEASE_BUCKET = "listing-studio-releases";
const LEGACY_MANIFEST_ASSET = "update.json";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_DIGEST_RE = /^sha256:([0-9a-f]{64})$/i;
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

function resolveInstallerAsset(release: any, version: string) {
  const candidates = [
    `EcommerceAgent-Setup-${version}.msi`,
    `EcommerceAgent-Setup-${version}.exe`,
  ];
  for (const installerName of candidates) {
    const asset = release.assets.find((item: any) => item?.name === installerName);
    if (asset?.browser_download_url) return { installerName, asset };
  }
  throw new Error("invalid_installer_asset");
}

async function resolveStableRelease() {
  const headers = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Listing-Studio-Release-Metadata",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  const releaseResponse = await fetch(RELEASE_API, { headers, cache: "no-store" });
  if (!releaseResponse.ok) throw new Error(`release_api_${releaseResponse.status}`);

  const release = await releaseResponse.json();
  if (!release || release.draft || release.prerelease || !Array.isArray(release.assets)) {
    throw new Error("invalid_latest_release");
  }

  const version = normalizeVersion(release.tag_name);
  if (!VERSION_RE.test(version) || String(release.tag_name || "") !== `v${version}`) {
    throw new Error("invalid_stable_tag");
  }

  const { installerName, asset: installerAsset } = resolveInstallerAsset(release, version);
  const installerSize = Number(installerAsset?.size || 0);
  const digest = String(installerAsset?.digest || "").trim().toLowerCase();
  const digestMatch = digest.match(SHA256_DIGEST_RE);
  if (
    !installerAsset?.browser_download_url ||
    !Number.isSafeInteger(installerSize) ||
    installerSize <= 0 ||
    !digestMatch
  ) {
    throw new Error("invalid_installer_asset");
  }

  let title = String(release.name || `Listing Studio ${version}`).trim();
  let notes = String(release.body || "").trim();
  let publishedAt = String(release.published_at || "").trim();
  let required = false;
  let minSupportedVersion = "";

  // Old Inno-era releases carried presentation metadata in update.json. Treat it
  // as optional transition metadata only; Velopack releases intentionally do not
  // publish it because legacy clients must not auto-cross-install the new format.
  const legacyManifestAsset = release.assets.find((asset: any) => asset?.name === LEGACY_MANIFEST_ASSET);
  if (legacyManifestAsset?.browser_download_url) {
    try {
      const manifestResponse = await fetch(String(legacyManifestAsset.browser_download_url), {
        headers: { "User-Agent": "Listing-Studio-Release-Metadata" },
        cache: "no-store",
        redirect: "follow",
      });
      if (manifestResponse.ok) {
        const manifest = await manifestResponse.json();
        if (
          manifest?.schema_version === 1 &&
          manifest?.channel === "stable" &&
          normalizeVersion(manifest?.version) === version
        ) {
          title = String(manifest?.title || title).trim();
          notes = String(manifest?.notes || notes).trim();
          publishedAt = String(manifest?.published_at || publishedAt).trim();
          required = Boolean(manifest?.required);
          minSupportedVersion = String(manifest?.min_supported_version || "").trim();
        }
      }
    } catch (error) {
      console.error("legacy release metadata ignored", error);
    }
  }

  return {
    version,
    title: title || `Listing Studio ${version}`,
    notes,
    publishedAt,
    required,
    minSupportedVersion,
    installerName,
    installerUrl: String(installerAsset.browser_download_url),
    installerSha256: digestMatch[1].toLowerCase(),
    installerSize,
    fileSize: formatBytes(installerSize),
  };
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
    EdgeRuntime.waitUntil(warmPrivateMirror(stable));

    return json(req, {
      channel: "stable",
      version: `v${stable.version}`,
      title: stable.title,
      notes: stable.notes,
      publishedAt: stable.publishedAt,
      required: stable.required,
      minSupportedVersion: stable.minSupportedVersion,
      fileName: stable.installerName,
      fileSize: stable.fileSize,
      fileSizeBytes: stable.installerSize,
      installerSha256: stable.installerSha256,
    });
  } catch (error) {
    console.error("public release metadata resolution failed", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
