import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);
const TASK_AUDIT_LIMIT = 300;
const DIAGNOSTIC_LIMIT = 160;
const SYSTEM_SAMPLE_LIMIT = 3000;
const SYSTEM_WINDOW_HOURS = 24;

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

function clients(req: Request) {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!url || !anonKey || !serviceRole) throw new Error("server_config");
  const authHeader = req.headers.get("Authorization") || "";
  const userClient = createClient(url, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const admin = createClient(url, serviceRole, { auth: { persistSession: false, autoRefreshToken: false } });
  return { userClient, admin };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);

  try {
    const { userClient, admin } = clients(req);
    const { data: userData, error: userError } = await userClient.auth.getUser();
    const user = userData?.user;
    if (userError || !user) return json(req, { error: "invalid_auth" }, 401);

    const { data: access, error: accessError } = await admin
      .from("download_portal_users")
      .select("enabled, is_admin")
      .eq("user_id", user.id)
      .maybeSingle();
    if (accessError) return json(req, { error: "access_check_failed" }, 503);
    if (!access?.enabled || !access?.is_admin) return json(req, { error: "not_authorized" }, 403);

    const systemSince = new Date(Date.now() - SYSTEM_WINDOW_HOURS * 60 * 60 * 1000).toISOString();
    const [
      { data: snapshot, error: snapshotError },
      { data: taskAudits, error: auditError },
      { data: diagnostics, error: diagnosticError },
      { data: systemSamples, error: systemError },
    ] = await Promise.all([
      admin.rpc("get_listing_usage_admin_snapshot", { p_caller: user.id }),
      admin
        .from("listing_task_audits")
        .select(
          "id,user_id,device_id,app_version,task_kind,phase,status,product_url,input_data,result_data,error_text,started_at,completed_at,updated_at,created_at"
        )
        .order("updated_at", { ascending: false })
        .limit(TASK_AUDIT_LIMIT),
      admin
        .from("listing_diagnostic_reports")
        .select("id,report_code,user_id,device_id,app_version,crash_id,startup_stage,report,created_at")
        .order("created_at", { ascending: false })
        .limit(DIAGNOSTIC_LIMIT),
      admin
        .from("listing_system_samples")
        .select("id,user_id,session_id,device_id,app_version,sample,occurred_at")
        .gte("occurred_at", systemSince)
        .order("occurred_at", { ascending: false })
        .limit(SYSTEM_SAMPLE_LIMIT),
    ]);
    if (snapshotError) return json(req, { error: "usage_snapshot_failed" }, 503);
    if (auditError) return json(req, { error: "task_audit_snapshot_failed" }, 503);
    if (diagnosticError) return json(req, { error: "diagnostic_snapshot_failed" }, 503);
    if (systemError) return json(req, { error: "system_health_snapshot_failed" }, 503);

    const payload = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
      ? { ...(snapshot as Record<string, unknown>) }
      : {};
    payload.task_audits = taskAudits ?? [];
    payload.task_audit_limit = TASK_AUDIT_LIMIT;
    payload.diagnostic_reports = diagnostics ?? [];
    payload.diagnostic_limit = DIAGNOSTIC_LIMIT;
    payload.system_samples = systemSamples ?? [];
    payload.system_sample_limit = SYSTEM_SAMPLE_LIMIT;
    payload.system_window_hours = SYSTEM_WINDOW_HOURS;
    payload.task_audit_scope = {
      includes: [
        "supplier URL",
        "sales specification / bundle intent",
        "AI guidance",
        "Model Name keywords",
        "requested Vertical",
        "customer file metadata",
        "resolved field outputs",
        "task status / errors",
        "batch job status",
        "AI model/cache/web statistics",
        "canonical execution report",
        "task duration",
        "crash diagnostics",
        "CPU / memory / disk / UI loop / Edge process health",
        "telemetry request latency",
      ],
      excludes: ["API keys", "access / refresh tokens", "passwords", "cookies", "authorization secrets", "raw customer file binaries"],
    };
    return json(req, payload);
  } catch {
    return json(req, { error: "server_error" }, 500);
  }
});
