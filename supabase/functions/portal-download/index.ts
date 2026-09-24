import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const REPOSITORY = "yuchenm1303-png/ecommerce-agent";
const ACCESS_TABLE = "download_portal_users";
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
  const expectedInstallerUrl = `https://github.com/${REPOSITORY}/releases/download/v${requestedVersion}/${installerName}`;
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

  if (String(body.action || "download") !== "download") {
    return json(req, { error: "invalid_action" }, 400);
  }

  const version = normalizeVersion(body.version);
  if (!VERSION_RE.test(version)) {
    return json(req, { error: "invalid_version" }, 400);
  }

  try {
    const stable = await resolveStableVersion(version);
    if ("error" in stable) {
      return json(req, { error: stable.error }, stable.status);
    }
    return json(req, {
      url: stable.url,
      version: `v${stable.version}`,
      sha256: stable.sha256,
      size: stable.size,
      publishedAt: stable.publishedAt,
      source: "github_release_stable_version",
    });
  } catch (error) {
    console.error("authorized stable version download resolution failed", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
