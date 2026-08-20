import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);
const TASK_AUDIT_LIMIT = 120;
const DIAGNOSTIC_LIMIT = 100;
const SYSTEM_WINDOW_HOURS = 24;
const SYSTEM_BUCKET_MINUTES = 5;
const DAILY_HEATMAP_DAYS = 365;

const TTL = {
  snapshot: 10_000,
  tasks: 20_000,
  system: 30_000,
  diagnostics: 60_000,
  heatmap: 5 * 60_000,
};

const cache = new Map<string, { value: unknown; expiresAt: number }>();
const inflight = new Map<string, Promise<{ value: unknown; stale: boolean }>>();

type JsonObject = Record<string, unknown>;
type Scope = "core" | "ops" | "heatmap" | "task_detail" | "full";

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
  return textValue(input.audit_scope) === "batch_link" ||
    textValue(result.audit_scope) === "batch_link" ||
    Boolean(textValue(input.job_id) && textValue(audit.product_url));
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
      source_audit_id: textValue(audit.id),
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
      normalized.push({ ...audit, source_audit_id: textValue(audit.id) });
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

function clients(req: Request) {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!url || !anonKey || !serviceRole) throw new Error("server_config");

  const authHeader = req.headers.get("Authorization") || "";
  return {
    userClient: createClient(url, anonKey, {
      global: { headers: { Authorization: authHeader } },
      auth: { persistSession: false, autoRefreshToken: false },
    }),
    admin: createClient(url, serviceRole, {
      auth: { persistSession: false, autoRefreshToken: false },
    }),
  };
}

async function dataOrThrow(request: PromiseLike<{ data: unknown; error: unknown }>, code: string): Promise<unknown> {
  const { data, error } = await request;
  if (error) throw new Error(code);
  return data;
}

async function cached(key: string, ttlMs: number, loader: () => Promise<unknown>): Promise<{ value: unknown; stale: boolean }> {
  const existing = cache.get(key);
  if (existing && existing.expiresAt > Date.now()) return { value: existing.value, stale: false };

  const running = inflight.get(key);
  if (running) return await running;

  const promise = (async () => {
    try {
      const value = await loader();
      cache.set(key, { value, expiresAt: Date.now() + ttlMs });
      return { value, stale: false };
    } catch (error) {
      if (existing) return { value: existing.value, stale: true };
      throw error;
    } finally {
      inflight.delete(key);
    }
  })();
  inflight.set(key, promise);
  return await promise;
}

async function loadSnapshot(admin: ReturnType<typeof createClient>, userId: string) {
  return await cached(`snapshot:${userId}`, TTL.snapshot, () =>
    dataOrThrow(admin.rpc("get_listing_usage_admin_snapshot", { p_caller: userId }), "usage_snapshot_failed"));
}

async function loadTasks(admin: ReturnType<typeof createClient>, userId: string) {
  return await cached(`tasks:${userId}`, TTL.tasks, async () => {
    const rows = await dataOrThrow(
      admin.from("listing_task_audits")
        .select("id,user_id,device_id,app_version,task_kind,phase,status,product_url,input_data,result_data,error_text,started_at,completed_at,updated_at,created_at")
        .order("updated_at", { ascending: false })
        .limit(TASK_AUDIT_LIMIT),
      "task_audit_snapshot_failed",
    );
    return normalizeTaskAudits(rows);
  });
}

async function loadDiagnostics(admin: ReturnType<typeof createClient>, userId: string) {
  return await cached(`diagnostics:${userId}`, TTL.diagnostics, () =>
    dataOrThrow(
      admin.from("listing_diagnostic_reports")
        .select("id,report_code,user_id,device_id,app_version,crash_id,startup_stage,report,created_at")
        .order("created_at", { ascending: false })
        .limit(DIAGNOSTIC_LIMIT),
      "diagnostic_snapshot_failed",
    ));
}

async function loadSystem(admin: ReturnType<typeof createClient>, userId: string) {
  return await cached(`system:${userId}`, TTL.system, () =>
    dataOrThrow(admin.rpc("get_listing_usage_system_samples", {
      p_caller: userId,
      p_hours: SYSTEM_WINDOW_HOURS,
      p_bucket_minutes: SYSTEM_BUCKET_MINUTES,
    }), "system_health_snapshot_failed"));
}

async function loadHeatmap(admin: ReturnType<typeof createClient>, userId: string) {
  return await cached(`heatmap:${userId}`, TTL.heatmap, () =>
    dataOrThrow(admin.rpc("get_listing_usage_daily_heatmap", {
      p_caller: userId,
      p_days: DAILY_HEATMAP_DAYS,
    }), "daily_heatmap_failed"));
}

function basePayload(snapshot: unknown): JsonObject {
  const payload = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
    ? { ...(snapshot as JsonObject) }
    : {};
  payload.query_architecture = "usage_monitor_persistent_read_model_v3";
  payload.system_window_hours = SYSTEM_WINDOW_HOURS;
  payload.system_bucket_minutes = SYSTEM_BUCKET_MINUTES;
  return payload;
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

    let body: JsonObject = {};
    try { body = objectValue(await req.json()); } catch { body = {}; }
    const requested = textValue(body.scope || body.mode).toLowerCase();
    const scope: Scope = requested === "core" || requested === "ops" || requested === "heatmap" || requested === "task_detail"
      ? requested as Scope
      : "full";

    if (scope === "task_detail") {
      const sourceId = textValue(body.source_audit_id || body.audit_id).split(":")[0];
      if (!sourceId) return json(req, { error: "audit_id_required" }, 400);
      const { data: row, error } = await admin.from("listing_task_audits")
        .select("id,user_id,device_id,app_version,task_kind,phase,status,product_url,input_data,result_data,error_text,started_at,completed_at,updated_at,created_at")
        .eq("id", sourceId)
        .maybeSingle();
      if (error) return json(req, { error: "task_detail_failed" }, 503);
      if (!row) return json(req, { error: "task_not_found" }, 404);
      const normalized = normalizeTaskAudits([row]);
      const logicalId = textValue(body.audit_id);
      const taskAudit = normalized.find((item) => textValue(item.id) === logicalId) ?? normalized[0] ?? row;
      return json(req, { query_architecture: "usage_monitor_persistent_read_model_v3", task_audit: taskAudit });
    }

    if (scope === "heatmap") {
      try {
        const heatmap = await loadHeatmap(admin, user.id);
        return json(req, {
          query_architecture: "usage_monitor_persistent_read_model_v3",
          daily_activity: heatmap.value,
          partial_errors: heatmap.stale ? [{ component: "daily_activity", code: "stale_cache" }] : [],
        });
      } catch {
        return json(req, { error: "daily_heatmap_failed" }, 503);
      }
    }

    let snapshot;
    try {
      snapshot = await loadSnapshot(admin, user.id);
    } catch {
      return json(req, { error: "usage_snapshot_failed" }, 503);
    }

    const payload = basePayload(snapshot.value);
    const partialErrors: JsonObject[] = [];
    if (snapshot.stale) partialErrors.push({ component: "summary", code: "stale_cache" });

    if (scope === "core") {
      try {
        const tasks = await loadTasks(admin, user.id);
        payload.task_audits = tasks.value;
        payload.task_audit_limit = TASK_AUDIT_LIMIT;
        if (tasks.stale) partialErrors.push({ component: "task_audits", code: "stale_cache" });
      } catch {
        payload.task_audits = [];
        payload.task_audit_limit = TASK_AUDIT_LIMIT;
        partialErrors.push({ component: "task_audits", code: "query_failed" });
      }
      payload.partial_errors = partialErrors;
      return json(req, payload);
    }

    if (scope === "ops") {
      const [tasksResult, diagnosticsResult, systemResult] = await Promise.allSettled([
        loadTasks(admin, user.id),
        loadDiagnostics(admin, user.id),
        loadSystem(admin, user.id),
      ]);
      payload.task_audits = tasksResult.status === "fulfilled" ? tasksResult.value.value : [];
      payload.diagnostic_reports = diagnosticsResult.status === "fulfilled" ? diagnosticsResult.value.value : [];
      payload.system_samples = systemResult.status === "fulfilled" ? systemResult.value.value : [];
      payload.task_audit_limit = TASK_AUDIT_LIMIT;
      payload.diagnostic_limit = DIAGNOSTIC_LIMIT;
      payload.system_sample_basis = "persistent_5m_rollup_v3";
      for (const [name, result] of [["task_audits", tasksResult], ["diagnostics", diagnosticsResult], ["system_health", systemResult]] as const) {
        if (result.status === "rejected") partialErrors.push({ component: name, code: "query_failed" });
        else if (result.value.stale) partialErrors.push({ component: name, code: "stale_cache" });
      }
      payload.partial_errors = partialErrors;
      return json(req, payload);
    }

    const [tasksResult, diagnosticsResult, systemResult, heatmapResult] = await Promise.allSettled([
      loadTasks(admin, user.id),
      loadDiagnostics(admin, user.id),
      loadSystem(admin, user.id),
      loadHeatmap(admin, user.id),
    ]);
    payload.task_audits = tasksResult.status === "fulfilled" ? tasksResult.value.value : [];
    payload.diagnostic_reports = diagnosticsResult.status === "fulfilled" ? diagnosticsResult.value.value : [];
    payload.system_samples = systemResult.status === "fulfilled" ? systemResult.value.value : [];
    payload.daily_activity = heatmapResult.status === "fulfilled"
      ? heatmapResult.value.value
      : { timezone: "Asia/Shanghai", window_days: DAILY_HEATMAP_DAYS, days: [] };
    payload.task_audit_limit = TASK_AUDIT_LIMIT;
    payload.diagnostic_limit = DIAGNOSTIC_LIMIT;
    payload.system_sample_basis = "persistent_5m_rollup_v3";
    payload.partial_errors = partialErrors;
    return json(req, payload);
  } catch {
    return json(req, { error: "server_error" }, 500);
  }
});
