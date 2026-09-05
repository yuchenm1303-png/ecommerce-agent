import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const ACCESS_TABLE = "download_portal_users";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_DIGEST_RE = /^sha256:([0-9a-f]{64})$/i;
const SHA256_HEX_RE = /^[0-9a-f]{64}$/i;
const PORTAL_RELEASE_TIMEOUT_MS = 5_000;
const ALLOWED_ORIGINS = new Set([
  "https://smirel.com",
  "https://www.smirel.com",
]);

function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("origin") || "";
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGINS.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
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
      "Cache-Control": "no-store",
    },
  });
}

function normalizeVersion(value: unknown): string {
  const raw = String(value ?? "").trim();
  return raw.startsWith("v") ? raw.slice(1) : raw;
}

function installerUrl(version: string): string {
  const name = `EcommerceAgent-Setup-${version}.exe`;
  return `https://github.com/${REPOSITORY}/releases/download/v${version}/${name}`;
}

async function getAuthorizedUser(req: Request) {
  const authHeader = req.headers.get("Authorization") || "";
  if (!authHeader.startsWith("Bearer ")) {
    return { error: "missing_auth", status: 401 } as const;
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const supabaseAnonKey = Deno.env.get("SUPABASE_ANON_KEY") || "";
  if (!supabaseUrl || !supabaseAnonKey) {
    return { error: "server_config", status: 500 } as const;
  }

  const client = createClient(supabaseUrl, supabaseAnonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const { data: userData, error: userError } = await client.auth.getUser();
  const user = userData?.user;
  if (userError || !user) {
    return { error: "invalid_auth", status: 401 } as const;
  }

  const { data: access, error: accessError } = await client
    .from(ACCESS_TABLE)
    .select("enabled, expires_at")
    .eq("user_id", user.id)
    .maybeSingle();

  if (accessError) {
    console.error("portal access query failed", accessError);
    return { error: "access_check_failed", status: 503 } as const;
  }
  if (!access || !access.enabled) {
    return { error: "not_authorized", status: 403 } as const;
  }
  if (access.expires_at && Date.parse(access.expires_at) <= Date.now()) {
    return { error: "access_expired", status: 403 } as const;
  }

  return { user, status: 200 } as const;
}

async function resolveLatestStable() {
  const supabaseUrl = String(Deno.env.get("SUPABASE_URL") || "").trim().replace(/\/$/, "");
  if (!supabaseUrl) throw new Error("server_config");

  const response = await fetch(`${supabaseUrl}/functions/v1/portal-release`, {
    method: "GET",
    headers: {
      "Accept": "application/json",
      "User-Agent": "Listing-Studio-Authorized-Download",
    },
    cache: "no-store",
    signal: AbortSignal.timeout(PORTAL_RELEASE_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`portal_release_${response.status}`);

  const release = await response.json();
  const version = normalizeVersion(release?.version);
  const sha256 = String(release?.installerSha256 || "").trim().toLowerCase();
  const size = Number(release?.fileSizeBytes || 0);
  if (
    release?.channel !== "stable" ||
    !VERSION_RE.test(version) ||
    !SHA256_HEX_RE.test(sha256) ||
    !Number.isSafeInteger(size) ||
    size <= 0
  ) {
    throw new Error("invalid_portal_stable_release");
  }

  return {
    version,
    url: installerUrl(version),
    sha256,
    size,
    publishedAt: String(release?.publishedAt || ""),
  } as const;
}

async function resolveStableVersion(requestedVersion: string) {
  const releaseApi = `https://api.github.com/repos/${REPOSITORY}/releases/tags/v${requestedVersion}`;
  const githubHeaders = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Listing-Studio-Authorized-Download",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const releaseResponse = await fetch(releaseApi, {
    headers: githubHeaders,
    cache: "no-store",
  });
  if (releaseResponse.status === 404) {
    return { error: "version_not_found", status: 404 } as const;
  }
  if (!releaseResponse.ok) throw new Error(`release_api_${releaseResponse.status}`);

  const release = await releaseResponse.json();
  if (
    !release ||
    release.draft ||
    release.prerelease ||
    !Array.isArray(release.assets) ||
    String(release.tag_name || "") !== `v${requestedVersion}`
  ) {
    return { error: "version_not_stable", status: 409 } as const;
  }

  const installerName = `EcommerceAgent-Setup-${requestedVersion}.exe`;
  const installerAsset = release.assets.find((asset: any) => asset?.name === installerName);
  const expectedInstallerUrl = installerUrl(requestedVersion);
  if (String(installerAsset?.browser_download_url || "") !== expectedInstallerUrl) {
    throw new Error("installer_url_mismatch");
  }

  const installerSize = Number(installerAsset?.size || 0);
  const digest = String(installerAsset?.digest || "").trim().toLowerCase();
  const digestMatch = digest.match(SHA256_DIGEST_RE);
  if (!Number.isSafeInteger(installerSize) || installerSize <= 0 || !digestMatch) {
    throw new Error("invalid_stable_installer_asset");
  }

  return {
    version: requestedVersion,
    url: expectedInstallerUrl,
    sha256: digestMatch[1].toLowerCase(),
    size: installerSize,
    publishedAt: String(release.published_at || release.created_at || ""),
  } as const;
}

async function resolveLegacyDownload(version: string) {
  if (VERSION_RE.test(version)) {
    const exact = await resolveStableVersion(version);
    if (!("error" in exact)) return { stable: exact, source: "github_release_stable_version" } as const;
    if (exact.error !== "version_not_found") return exact;
  }

  // Old cached portal JS used a packaged fallback version for the "latest" button.
  // If that stale tag no longer exists, recover by asking the server-owned Stable
  // control plane instead of returning a false 404 to the user.
  return {
    stable: await resolveLatestStable(),
    source: "portal_release_stable_latest_legacy_recovery",
  } as const;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }
  if (req.method !== "POST") {
    return json(req, { error: "method_not_allowed" }, 405);
  }

  const auth = await getAuthorizedUser(req);
  if ("error" in auth) {
    return json(req, { error: auth.error }, auth.status);
  }

  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch {
    return json(req, { error: "invalid_json" }, 400);
  }

  const action = String(body.action || "download").trim();
  const version = normalizeVersion(body.version);

  try {
    let resolved:
      | { stable: Awaited<ReturnType<typeof resolveLatestStable>>; source: string }
      | { error: string; status: number };

    if (action === "download_latest") {
      resolved = {
        stable: await resolveLatestStable(),
        source: "portal_release_stable_latest",
      };
    } else if (action === "download_version") {
      if (!VERSION_RE.test(version)) {
        return json(req, { error: "invalid_version" }, 400);
      }
      const exact = await resolveStableVersion(version);
      if ("error" in exact) {
        resolved = exact;
      } else {
        resolved = { stable: exact, source: "github_release_stable_version" };
      }
    } else if (action === "download") {
      // Backward compatibility for already-cached portal JS. New clients must use
      // one of the explicit actions above so intent is never inferred from a version.
      resolved = await resolveLegacyDownload(version);
    } else {
      return json(req, { error: "invalid_action" }, 400);
    }

    if ("error" in resolved) {
      return json(req, { error: resolved.error }, resolved.status);
    }

    const stable = resolved.stable;
    return json(req, {
      url: stable.url,
      version: `v${stable.version}`,
      sha256: stable.sha256,
      size: stable.size,
      publishedAt: stable.publishedAt,
      source: resolved.source,
    });
  } catch (error) {
    console.error("authorized stable download resolution failed", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
