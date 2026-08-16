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
    headers: { ...corsHeaders(req), "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
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
  return createClient(supabaseUrl, serviceRole, { auth: { persistSession: false, autoRefreshToken: false } });
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((part) => part.toString(16).padStart(2, "0")).join("");
}

function randomTelemetryToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);

  const auth = await authenticatedUser(req);
  if ("error" in auth) return json(req, { error: auth.error }, auth.status);

  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { body = {}; }

  const action = String(body.action || "validate").trim();
  const deviceId = String(body.device_id || "").trim().toLowerCase();
  const deviceName = String(body.device_name || "").trim().slice(0, 160);
  const appVersion = String(body.app_version || "").trim().slice(0, 64);
  const fingerprintVersion = Math.max(1, Math.min(32767, Number(body.fingerprint_version || 1) || 1));

  if (!DEVICE_RE.test(deviceId)) return json(req, { error: "invalid_device_id" }, 400);
  if (!["activate", "validate", "deactivate"].includes(action)) return json(req, { error: "invalid_action" }, 400);

  const admin = adminClient();
  const now = new Date();
  const nowIso = now.toISOString();

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
    return json(req, { released: true, device_id: deviceId, active_devices: count || 0, released_at: nowIso });
  }

  const { data: access, error: accessError } = await admin
    .from(ACCESS_TABLE)
    .select("enabled, display_name, expires_at, max_devices, grace_period_hours")
    .eq("user_id", auth.user.id)
    .maybeSingle();
  if (accessError) return json(req, { error: "access_check_failed" }, 503);
  if (!access || !access.enabled) return json(req, { error: "not_authorized" }, 403);
  if (access.expires_at && Date.parse(access.expires_at) <= now.getTime()) return json(req, { error: "access_expired" }, 403);

  const { data: existing, error: existingError } = await admin
    .from(DEVICE_TABLE)
    .select("enabled, revoked_at")
    .eq("user_id", auth.user.id)
    .eq("device_id", deviceId)
    .maybeSingle();
  if (existingError) return json(req, { error: "device_check_failed" }, 503);
  if (existing?.revoked_at) return json(req, { error: "device_revoked" }, 403);

  const telemetryToken = randomTelemetryToken();
  const telemetryTokenHash = await sha256Hex(telemetryToken);
  const maxDevices = Math.max(1, Number(access.max_devices || 2));

  if (!existing || !existing.enabled) {
    if (action !== "activate") return json(req, { error: "device_not_activated" }, 403);

    const { count, error: countError } = await admin
      .from(DEVICE_TABLE)
      .select("device_id", { count: "exact", head: true })
      .eq("user_id", auth.user.id)
      .eq("enabled", true)
      .is("revoked_at", null);
    if (countError) return json(req, { error: "device_check_failed" }, 503);
    if ((count || 0) >= maxDevices) return json(req, { error: "device_limit_reached", max_devices: maxDevices, active_devices: count || 0 }, 403);

    if (!existing) {
      const { error } = await admin.from(DEVICE_TABLE).insert({
        user_id: auth.user.id,
        device_id: deviceId,
        device_name: deviceName,
        fingerprint_version: fingerprintVersion,
        enabled: true,
        app_version: appVersion,
        telemetry_token_hash: telemetryTokenHash,
        first_seen_at: nowIso,
        last_seen_at: nowIso,
        created_at: nowIso,
        updated_at: nowIso,
      });
      if (error) return json(req, { error: "device_activation_failed" }, 503);
    } else {
      const { error } = await admin.from(DEVICE_TABLE).update({
        enabled: true,
        device_name: deviceName,
        fingerprint_version: fingerprintVersion,
        app_version: appVersion,
        telemetry_token_hash: telemetryTokenHash,
        last_seen_at: nowIso,
        updated_at: nowIso,
      }).eq("user_id", auth.user.id).eq("device_id", deviceId);
      if (error) return json(req, { error: "device_activation_failed" }, 503);
    }
  } else {
    const { error } = await admin.from(DEVICE_TABLE).update({
      device_name: deviceName,
      fingerprint_version: fingerprintVersion,
      app_version: appVersion,
      telemetry_token_hash: telemetryTokenHash,
      last_seen_at: nowIso,
      updated_at: nowIso,
    }).eq("user_id", auth.user.id).eq("device_id", deviceId);
    if (error) return json(req, { error: "device_renewal_failed" }, 503);
  }

  const { count: activeCount } = await admin
    .from(DEVICE_TABLE)
    .select("device_id", { count: "exact", head: true })
    .eq("user_id", auth.user.id)
    .eq("enabled", true)
    .is("revoked_at", null);

  return json(req, {
    authorized: true,
    user_id: auth.user.id,
    email: auth.user.email || "",
    display_name: access.display_name || "",
    expires_at: access.expires_at,
    max_devices: maxDevices,
    active_devices: activeCount || 0,
    grace_period_hours: Math.max(0, Number(access.grace_period_hours || 72)),
    device_id: deviceId,
    telemetry_token: telemetryToken,
    validated_at: nowIso,
  });
});
