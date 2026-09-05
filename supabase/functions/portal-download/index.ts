import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const ACCESS_TABLE = "download_portal_users";
const DOWNLOAD_BUCKET = "listing-studio-downloads";
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const SHA256_HEX_RE = /^[0-9a-f]{64}$/i;
const PORTAL_RELEASE_TIMEOUT_MS = 5_000;
const SIGNED_CHUNK_TTL_SECONDS = 60 * 60;
const CHUNK_MANIFEST_SCHEMA = 1;
const MAX_CHUNKS = 64;
const ALLOWED_ORIGINS = new Set([
  "https://smirel.com",
  "https://www.smirel.com",
]);

type InstallerChunk = {
  name: string;
  offset: number;
  size: number;
};

type InstallerManifest = {
  schema_version: number;
  version: string;
  file_name: string;
  size: number;
  sha256: string;
  chunk_size: number;
  chunks: InstallerChunk[];
};

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
  const supabaseUrl = String(Deno.env.get("SUPABASE_URL") || "").trim().replace(/\/$/, "");
  const serverKey = getServerSecretKey();
  if (!supabaseUrl || !serverKey) throw new Error("server_storage_config_missing");
  return createClient(supabaseUrl, serverKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
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
      Accept: "application/json",
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
    sha256,
    size,
    publishedAt: String(release?.publishedAt || ""),
  } as const;
}

function installerFileName(version: string): string {
  return `EcommerceAgent-Setup-${version}.exe`;
}

function installerFolder(version: string): string {
  return `stable/v${version}/${installerFileName(version)}`;
}

async function loadInstallerManifest(version: string): Promise<InstallerManifest | null> {
  const admin = createAdminClient();
  const folder = installerFolder(version);
  const { data, error } = await admin.storage.from(DOWNLOAD_BUCKET).download(`${folder}/manifest.json`);
  if (error || !data) return null;

  let manifest: any;
  try {
    manifest = JSON.parse(await data.text());
  } catch {
    throw new Error("installer_manifest_invalid_json");
  }

  const fileName = installerFileName(version);
  const size = Number(manifest?.size || 0);
  const sha256 = String(manifest?.sha256 || "").trim().toLowerCase();
  const chunks = Array.isArray(manifest?.chunks) ? manifest.chunks : [];
  if (
    manifest?.schema_version !== CHUNK_MANIFEST_SCHEMA ||
    manifest?.version !== `v${version}` ||
    manifest?.file_name !== fileName ||
    !Number.isSafeInteger(size) || size <= 0 ||
    !SHA256_HEX_RE.test(sha256) ||
    chunks.length <= 0 || chunks.length > MAX_CHUNKS
  ) {
    throw new Error("installer_manifest_invalid");
  }

  let expectedOffset = 0;
  const normalized: InstallerChunk[] = [];
  for (let index = 0; index < chunks.length; index += 1) {
    const chunk = chunks[index];
    const name = String(chunk?.name || "");
    const offset = Number(chunk?.offset);
    const chunkSize = Number(chunk?.size);
    if (
      name !== `part-${String(index).padStart(3, "0")}` ||
      !Number.isSafeInteger(offset) || offset !== expectedOffset ||
      !Number.isSafeInteger(chunkSize) || chunkSize <= 0
    ) {
      throw new Error("installer_manifest_chunk_invalid");
    }
    normalized.push({ name, offset, size: chunkSize });
    expectedOffset += chunkSize;
  }
  if (expectedOffset !== size) throw new Error("installer_manifest_coverage_invalid");

  return {
    schema_version: CHUNK_MANIFEST_SCHEMA,
    version: `v${version}`,
    file_name: fileName,
    size,
    sha256,
    chunk_size: Number(manifest?.chunk_size || 0),
    chunks: normalized,
  };
}

async function signInstallerManifest(manifest: InstallerManifest) {
  const admin = createAdminClient();
  const version = normalizeVersion(manifest.version);
  const folder = installerFolder(version);
  const paths = manifest.chunks.map((chunk) => `${folder}/${chunk.name}`);
  const { data, error } = await admin.storage
    .from(DOWNLOAD_BUCKET)
    .createSignedUrls(paths, SIGNED_CHUNK_TTL_SECONDS);
  if (error || !Array.isArray(data) || data.length !== paths.length) {
    throw error || new Error("signed_chunk_urls_failed");
  }

  return manifest.chunks.map((chunk, index) => {
    const signed = data[index] as any;
    const signedUrl = String(signed?.signedUrl || "").trim();
    if (!signedUrl || signed?.error) throw new Error(`signed_chunk_url_invalid:${chunk.name}`);
    return {
      index,
      size: chunk.size,
      url: signedUrl,
    };
  });
}

async function buildDelivery(version: string, expected?: { size: number; sha256: string; publishedAt: string }) {
  const manifest = await loadInstallerManifest(version);
  if (!manifest) return { error: "version_not_mirrored", status: 404 } as const;

  if (expected) {
    if (manifest.size !== expected.size || manifest.sha256 !== expected.sha256) {
      throw new Error("latest_installer_mirror_mismatch");
    }
  }

  const chunks = await signInstallerManifest(manifest);
  return {
    delivery: "chunked" as const,
    fileName: manifest.file_name,
    version: manifest.version,
    sha256: manifest.sha256,
    size: manifest.size,
    publishedAt: expected?.publishedAt || "",
    expiresIn: SIGNED_CHUNK_TTL_SECONDS,
    chunks,
  };
}

async function resolveLegacyDownload(version: string) {
  if (VERSION_RE.test(version)) {
    const exact = await buildDelivery(version);
    if (!("error" in exact)) return exact;
  }
  const latest = await resolveLatestStable();
  return await buildDelivery(latest.version, latest);
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
    let delivery:
      | Awaited<ReturnType<typeof buildDelivery>>
      | Awaited<ReturnType<typeof resolveLegacyDownload>>;

    if (action === "download_latest") {
      const latest = await resolveLatestStable();
      delivery = await buildDelivery(latest.version, latest);
    } else if (action === "download_version") {
      if (!VERSION_RE.test(version)) {
        return json(req, { error: "invalid_version" }, 400);
      }
      delivery = await buildDelivery(version);
    } else if (action === "download") {
      delivery = await resolveLegacyDownload(version);
    } else {
      return json(req, { error: "invalid_action" }, 400);
    }

    if ("error" in delivery) {
      return json(req, { error: delivery.error }, delivery.status);
    }

    return json(req, delivery);
  } catch (error) {
    console.error("authorized Stable download delivery failed", error);
    return json(req, { error: "stable_release_unavailable" }, 503);
  }
});
