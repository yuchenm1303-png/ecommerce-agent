import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ACCESS_TABLE = "download_portal_users";
const DEVICE_TABLE = "download_portal_devices";
const DEVICE_RE = /^[0-9a-f]{32,128}$/i;
const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);

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

async function authenticatedUser(req: Request) {
  const authHeader = req.headers.get("Authorization") || "";
  if (!authHeader.startsWith("Bearer ")) return { error: "missing_auth", status: 401 } as const;
  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY") || "";
  if (!supabaseUrl || !anonKey) return { error: "server_config", status: 500 } as const;
  const client = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data, error } = await client.auth.getUser();
  if (error || !data?.user) return { error: "invalid_auth", status: 401 } as const;
  return { user: data.user, status: 200 } as const;
}

function adminClient() {
  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!supabaseUrl || !serviceRole) throw new Error("server_config");
  return createClient(supabaseUrl, serviceRole, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((part) => part.toString(16).padStart(2, "0"))
    .join("");
}

function randomTelemetryToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function rpcErrorStatus(error: string): number {
  if (error === "invalid_device") return 400;
  if (
    error === "not_authorized" ||
    error === "access_expired" ||
    error === "device_revoked" ||
    error === "device_not_activated" ||
    error === "device_limit_reached"
  ) return 403;
  return 503;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);

  const auth = await authenticatedUser(req);
  if ("error" in auth) return json(req, { error: auth.error }, auth.status);

  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const action = String(body.action || "validate").trim();
  const deviceId = String(body.device_id || "").trim().toLowerCase();
  const deviceName = String(body.device_name || "").trim().slice(0, 160);
  const appVersion = String(body.app_version || "").trim().slice(0, 64);
  const fingerprintVersion = Math.max(
    1,
    Math.min(32767, Number(body.fingerprint_version || 1) || 1),
  );

  if (!DEVICE_RE.test(deviceId)) return json(req, { error: "invalid_device_id" }, 400);
  if (!["activate", "validate", "deactivate"].includes(action)) {
    return json(req, { error: "invalid_action" }, 400);
  }

  const admin = adminClient();
  const nowIso = new Date().toISOString();

  if (action === "deactivate") {
    const { data: owned, error: ownedError } = await admin
      .from(DEVICE_TABLE)
      .select("revoked_at")
      .eq("user_id", auth.user.id)
      .eq("device_id", deviceId)
      .maybeSingle();
    if (ownedError) return json(req, { error: "device_check_failed" }, 503);
    if (owned?.revoked_at) return json(req, { error: "device_revoked" }, 403);

    const { error: releaseError } = await admin
      .from(DEVICE_TABLE)
      .update({ enabled: false, telemetry_token_hash: null, updated_at: nowIso })
      .eq("user_id", auth.user.id)
      .eq("device_id", deviceId);
    if (releaseError) return json(req, { error: "device_release_failed" }, 503);

    const { count } = await admin
      .from(DEVICE_TABLE)
      .select("device_id", { count: "exact", head: true })
      .eq("user_id", auth.user.id)
      .eq("enabled", true)
      .is("revoked_at", null);
    return json(req, {
      released: true,
      device_id: deviceId,
      active_devices: count || 0,
      released_at: nowIso,
    });
  }

  // Token generation stays at the trusted Edge boundary. Only its hash reaches
  // the database. Slot allocation and access validation are one serialized DB
  // transaction so concurrent activate requests cannot both consume the same
  // final device slot.
  const telemetryToken = randomTelemetryToken();
  const telemetryTokenHash = await sha256Hex(telemetryToken);
  const { data: rpcData, error: rpcError } = await admin.rpc(
    "activate_listing_portal_device_v1",
    {
      p_user_id: auth.user.id,
      p_device_id: deviceId,
      p_device_name: deviceName,
      p_fingerprint_version: fingerprintVersion,
      p_app_version: appVersion,
      p_telemetry_token_hash: telemetryTokenHash,
      p_activate: action === "activate",
    },
  );
  if (rpcError) return json(req, { error: "device_activation_failed" }, 503);

  const result = rpcData && typeof rpcData === "object"
    ? rpcData as Record<string, unknown>
    : {};
  const resultError = String(result.error || "").trim();
  if (resultError) return json(req, result, rpcErrorStatus(resultError));
  if (!result.authorized) return json(req, { error: "not_authorized" }, 403);

  return json(req, {
    ...result,
    user_id: auth.user.id,
    email: auth.user.email || "",
    device_id: deviceId,
    telemetry_token: telemetryToken,
  });
});
