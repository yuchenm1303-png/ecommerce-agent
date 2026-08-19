import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);
const TASK_AUDIT_LIMIT = 300;
const DIAGNOSTIC_LIMIT = 160;
const SYSTEM_SAMPLE_LIMIT = 3000;
const SYSTEM_WINDOW_HOURS = 24;
const HOUR_MS = 60 * 60 * 1000;

type JsonObject = Record<string, unknown>;

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function textValue(value: unknown): string {
  return String(value ?? "").trim();
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function auditMinute(value: unknown): number {
  const stamp = Date.parse(textValue(value));
  return Number.isFinite(stamp) ? Math.floor(stamp / 60_000) : 0;
}

function jobAuditStatus(phase: unknown, jobStatus: unknown, fallback: unknown): string {
  const status = textValue(jobStatus).toUpperCase();
  if (textValue(phase).toLowerCase() === "batch_execute") {
    if (status === "DONE") return "completed";
    if (status === "REVIEW") return "review";
    if (status === "FAILED") return "failed";
    if (status === "STOPPED") return "cancelled";
    return textValue(fallback).toLowerCase() || "running";
  }
  if (status === "READY") return "ready";
  if (status === "REVIEW") return "review";
  if (status === "FAILED") return "failed";
  if (status === "STOPPED") return "cancelled";
  return textValue(fallback).toLowerCase() || "running";
}

function nativeBatchLink(audit: JsonObject): boolean {
  if (textValue(audit.task_kind) !== "batch") return false;
  const input = objectValue(audit.input_data);
  const result = objectValue(audit.result_data);
  return (
    textValue(input.audit_scope) === "batch_link" ||
    textValue(result.audit_scope) === "batch_link" ||
    Boolean(textValue(input.job_id) && textValue(audit.product_url))
  );
}

function batchDedupeKey(audit: JsonObject): string {
  const input = objectValue(audit.input_data);
  return [
    textValue(audit.user_id),
    textValue(audit.device_id),
    textValue(audit.product_url) || textValue(input.supplier_url),
    textValue(audit.phase).toLowerCase(),
    String(auditMinute(audit.started_at)),
  ].join("|");
}

function explodeLegacyBatchAudit(audit: JsonObject): JsonObject[] {
  const input = objectValue(audit.input_data);
  const result = objectValue(audit.result_data);
  const items = Array.isArray(input.items) ? input.items.map(objectValue) : [];
  const jobs = Array.isArray(result.jobs) ? result.jobs.map(objectValue) : [];
  const count = Math.max(jobs.length, items.length, numberValue(input.item_count), numberValue(result.job_count));
  if (!count) return [audit];

  const diagnostics = Array.isArray(result.failure_diagnostics)
    ? result.failure_diagnostics.map(objectValue)
    : [];
  const output: JsonObject[] = [];
  for (let index = 0; index < count; index += 1) {
    const job = jobs[index] ?? {};
    const jobUrl = textValue(job.product_url);
    const item = items.find((candidate) => jobUrl && textValue(candidate.supplier_url) === jobUrl) ?? items[index] ?? {};
    const jobId = textValue(job.job_id) || `JOB-${String(index + 1).padStart(3, "0")}`;
    const diagnostic = diagnostics.find((candidate) => textValue(candidate.job_id) === jobId);
    const productUrl = jobUrl || textValue(item.supplier_url);
    output.push({
      ...audit,
      id: `${textValue(audit.id) || "legacy-batch"}:${jobId}`,
      status: jobAuditStatus(audit.phase, job.status, audit.status),
      product_url: productUrl,
      input_data: {
        audit_scope: "batch_link_legacy",
        batch_id: textValue(input.batch_id) || textValue(result.batch_id) || textValue(audit.id),
        job_id: jobId,
        batch_index: index + 1,
        batch_size: count,
        supplier_url: productUrl,
        listing_intent: textValue(item.listing_intent),
        customer_files: Array.isArray(item.customer_files) ? item.customer_files : [],
        model_config: objectValue(input.model_config),
      },
      result_data: {
        ...job,
        audit_scope: "batch_link_legacy",
        batch_id: textValue(result.batch_id) || textValue(input.batch_id) || textValue(audit.id),
        job_id: jobId,
        batch_index: index + 1,
        batch_size: count,
        job_status: textValue(job.status),
        product_url: productUrl,
        ...(diagnostic ? { failure_diagnostic: diagnostic } : {}),
      },
      error_text: textValue(job.error) || textValue(audit.error_text),
    });
  }
  return output;
}

function normalizeTaskAudits(rawValue: unknown): JsonObject[] {
  const raw = Array.isArray(rawValue) ? rawValue.map(objectValue) : [];
  const nativeKeys = new Set(raw.filter(nativeBatchLink).map(batchDedupeKey));
  const normalized: JsonObject[] = [];

  for (const audit of raw) {
    if (textValue(audit.task_kind) !== "batch" || nativeBatchLink(audit)) {
      normalized.push(audit);
      continue;
    }
    for (const child of explodeLegacyBatchAudit(audit)) {
      if (nativeKeys.has(batchDedupeKey(child))) continue;
      normalized.push(child);
    }
  }

  return normalized.sort((left, right) => {
    const leftStamp = Date.parse(textValue(left.updated_at) || textValue(left.created_at));
    const rightStamp = Date.parse(textValue(right.updated_at) || textValue(right.created_at));
    return (Number.isFinite(rightStamp) ? rightStamp : 0) - (Number.isFinite(leftStamp) ? leftStamp : 0);
  });
}

function withIndependentProductActivity(usersValue: unknown, audits: JsonObject[]): JsonObject[] {
  const users = Array.isArray(usersValue) ? usersValue.map(objectValue) : [];
  const counts = new Map<string, Map<number, { completed: number; failed: number }>>();

  for (const audit of audits) {
    const status = textValue(audit.status).toLowerCase();
    const successful = status === "completed" || status === "ready";
    const failed = status === "failed" || status === "cancelled";
    if (!successful && !failed) continue;

    const userId = textValue(audit.user_id);
    if (!userId) continue;
    const stamp = Date.parse(
      textValue(audit.completed_at) || textValue(audit.updated_at) || textValue(audit.created_at),
    );
    if (!Number.isFinite(stamp)) continue;
    const hour = Math.floor(stamp / HOUR_MS);
    const byHour = counts.get(userId) ?? new Map<number, { completed: number; failed: number }>();
    const bucket = byHour.get(hour) ?? { completed: 0, failed: 0 };
    if (successful) bucket.completed += 1;
    if (failed) bucket.failed += 1;
    byHour.set(hour, bucket);
    counts.set(userId, byHour);
  }

  return users.map((user) => {
    const userId = textValue(user.user_id);
    const byHour = counts.get(userId) ?? new Map<number, { completed: number; failed: number }>();
    const activity = Array.isArray(user.activity_24h) ? user.activity_24h.map(objectValue) : [];
    return {
      ...user,
      activity_24h: activity.map((rawBucket) => {
        const stamp = Date.parse(textValue(rawBucket.bucket_start));
        const hour = Number.isFinite(stamp) ? Math.floor(stamp / HOUR_MS) : Number.NaN;
        const taskCounts = Number.isFinite(hour) ? byHour.get(hour) : undefined;
        return {
          ...rawBucket,
          completed: taskCounts?.completed ?? 0,
          failed: taskCounts?.failed ?? 0,
        };
      }),
    };
  });
}

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

    const systemSince = new Date(Date.now() - SYSTEM_WINDOW_HOURS * HOUR_MS).toISOString();
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
    const normalizedTaskAudits = normalizeTaskAudits(taskAudits ?? []);
    payload.users = withIndependentProductActivity(payload.users, normalizedTaskAudits);
    payload.task_audits = normalizedTaskAudits;
    payload.task_audit_raw_count = Array.isArray(taskAudits) ? taskAudits.length : 0;
    payload.task_audit_limit = TASK_AUDIT_LIMIT;
    payload.task_metric_basis = "independent_product_audits";
    payload.diagnostic_reports = diagnostics ?? [];
    payload.diagnostic_limit = DIAGNOSTIC_LIMIT;
    payload.system_samples = systemSamples ?? [];
    payload.system_sample_limit = SYSTEM_SAMPLE_LIMIT;
    payload.system_window_hours = SYSTEM_WINDOW_HOURS;
    payload.task_audit_scope = {
      includes: [
        "one independent audit per supplier product link",
        "per-product 24h success/failure chart counts",
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
