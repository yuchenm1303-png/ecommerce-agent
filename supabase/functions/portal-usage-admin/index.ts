import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);

const TASK_AUDIT_LIMIT = 200;
const DIAGNOSTIC_LIMIT = 160;
const SYSTEM_WINDOW_HOURS = 24;
const SYSTEM_BUCKET_MINUTES = 5;
const DAILY_HEATMAP_DAYS = 365;

const SNAPSHOT_TTL_MS = 10_000;
const TASKS_TTL_MS = 15_000;
const SYSTEM_TTL_MS = 30_000;
const DIAGNOSTICS_TTL_MS = 30_000;
const HEATMAP_TTL_MS = 5 * 60_000;

const componentCache = new Map();
const componentInflight = new Map();

function objectValue(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function textValue(value) {
  return String(value ?? "").trim();
}

function numberValue(value) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function errorMessage(error) {
  if (error instanceof Error) return error.message;
  return textValue(error) || "unknown_error";
}

function auditMinute(value) {
  const stamp = Date.parse(textValue(value));
  return Number.isFinite(stamp) ? Math.floor(stamp / 60_000) : 0;
}

function jobAuditStatus(phase, jobStatus, fallback) {
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

function nativeBatchLink(audit) {
  if (textValue(audit.task_kind) !== "batch") return false;
  const input = objectValue(audit.input_data);
  const result = objectValue(audit.result_data);
  return (
    textValue(input.audit_scope) === "batch_link" ||
    textValue(result.audit_scope) === "batch_link" ||
    Boolean(textValue(input.job_id) && textValue(audit.product_url))
  );
}

function batchDedupeKey(audit) {
  const input = objectValue(audit.input_data);
  return [
    textValue(audit.user_id),
    textValue(audit.device_id),
    textValue(audit.product_url) || textValue(input.supplier_url),
    textValue(audit.phase).toLowerCase(),
    String(auditMinute(audit.started_at)),
  ].join("|");
}

function explodeLegacyBatchAudit(audit) {
  const input = objectValue(audit.input_data);
  const result = objectValue(audit.result_data);
  const items = Array.isArray(input.items) ? input.items.map(objectValue) : [];
  const jobs = Array.isArray(result.jobs) ? result.jobs.map(objectValue) : [];
  const count = Math.max(
    jobs.length,
    items.length,
    numberValue(input.item_count),
    numberValue(result.job_count),
  );
  if (!count) return [audit];

  const diagnostics = Array.isArray(result.failure_diagnostics)
    ? result.failure_diagnostics.map(objectValue)
    : [];
  const output = [];

  for (let index = 0; index < count; index += 1) {
    const job = jobs[index] ?? {};
    const jobUrl = textValue(job.product_url);
    const item =
      items.find((candidate) => jobUrl && textValue(candidate.supplier_url) === jobUrl) ??
      items[index] ??
      {};
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

function normalizeTaskAudits(rawValue) {
  const raw = Array.isArray(rawValue) ? rawValue.map(objectValue) : [];
  const nativeKeys = new Set(raw.filter(nativeBatchLink).map(batchDedupeKey));
  const normalized = [];

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
    return (Number.isFinite(rightStamp) ? rightStamp : 0) -
      (Number.isFinite(leftStamp) ? leftStamp : 0);
  });
}

function corsHeaders(req) {
  const origin = req.headers.get("origin") || "";
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGINS.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function json(req, body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(req),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function clients(req) {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!url || !anonKey || !serviceRole) throw new Error("server_config");

  const authHeader = req.headers.get("Authorization") || "";
  const userClient = createClient(url, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const admin = createClient(url, serviceRole, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return { userClient, admin };
}

async function dataOrThrow(request, code) {
  const { data, error } = await request;
  if (error) {
    const message = textValue(error.message) || textValue(error.code) || "query_failed";
    throw new Error(`${code}:${message}`);
  }
  return data;
}

async function cachedComponent(key, ttlMs, loader) {
  const now = Date.now();
  const cached = componentCache.get(key);
  if (cached && cached.expiresAt > now) {
    return { value: cached.value, stale: false, cacheHit: true };
  }

  const running = componentInflight.get(key);
  if (running) return await running;

  const promise = (async () => {
    try {
      const value = await loader();
      componentCache.set(key, {
        value,
        expiresAt: Date.now() + ttlMs,
        storedAt: Date.now(),
      });
      return { value, stale: false, cacheHit: false };
    } catch (error) {
      if (cached) {
        return {
          value: cached.value,
          stale: true,
          cacheHit: true,
          error: errorMessage(error),
        };
      }
      throw error;
    } finally {
      componentInflight.delete(key);
    }
  })();

  componentInflight.set(key, promise);
  return await promise;
}

function readSettled(result, name, fallback, partialErrors) {
  if (result.status === "rejected") {
    partialErrors.push({ component: name, code: "query_failed" });
    return fallback;
  }
  if (result.value.stale) {
    partialErrors.push({ component: name, code: "stale_cache" });
  }
  return result.value.value;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }
  if (req.method !== "POST") {
    return json(req, { error: "method_not_allowed" }, 405);
  }

  try {
    const { userClient, admin } = clients(req);

    const { data: userData, error: userError } = await userClient.auth.getUser();
    const user = userData?.user;
    if (userError || !user) {
      return json(req, { error: "invalid_auth" }, 401);
    }

    const { data: access, error: accessError } = await admin
      .from("download_portal_users")
      .select("enabled, is_admin")
      .eq("user_id", user.id)
      .maybeSingle();

    if (accessError) {
      return json(req, { error: "access_check_failed" }, 503);
    }
    if (!access?.enabled || !access?.is_admin) {
      return json(req, { error: "not_authorized" }, 403);
    }

    const partialErrors = [];

    let snapshotComponent;
    try {
      snapshotComponent = await cachedComponent(
        `snapshot:${user.id}`,
        SNAPSHOT_TTL_MS,
        () => dataOrThrow(
          admin.rpc("get_listing_usage_admin_snapshot", { p_caller: user.id }),
          "usage_snapshot_failed",
        ),
      );
    } catch {
      return json(req, { error: "usage_snapshot_failed" }, 503);
    }

    if (snapshotComponent.stale) {
      partialErrors.push({ component: "summary", code: "stale_cache" });
    }

    const [heatmapResult, auditsResult, diagnosticsResult, systemResult] = await Promise.allSettled([
      cachedComponent(
        `heatmap:${user.id}:${DAILY_HEATMAP_DAYS}`,
        HEATMAP_TTL_MS,
        () => dataOrThrow(
          admin.rpc("get_listing_usage_daily_heatmap", {
            p_caller: user.id,
            p_days: DAILY_HEATMAP_DAYS,
          }),
          "daily_heatmap_failed",
        ),
      ),
      cachedComponent(
        `audits:${user.id}`,
        TASKS_TTL_MS,
        () => dataOrThrow(
          admin
            .from("listing_task_audits")
            .select(
              "id,user_id,device_id,app_version,task_kind,phase,status,product_url,input_data,result_data,error_text,started_at,completed_at,updated_at,created_at",
            )
            .order("updated_at", { ascending: false })
            .limit(TASK_AUDIT_LIMIT),
          "task_audit_snapshot_failed",
        ),
      ),
      cachedComponent(
        `diagnostics:${user.id}`,
        DIAGNOSTICS_TTL_MS,
        () => dataOrThrow(
          admin
            .from("listing_diagnostic_reports")
            .select("id,report_code,user_id,device_id,app_version,crash_id,startup_stage,report,created_at")
            .order("created_at", { ascending: false })
            .limit(DIAGNOSTIC_LIMIT),
          "diagnostic_snapshot_failed",
        ),
      ),
      cachedComponent(
        `system:${user.id}:${SYSTEM_WINDOW_HOURS}:${SYSTEM_BUCKET_MINUTES}`,
        SYSTEM_TTL_MS,
        () => dataOrThrow(
          admin.rpc("get_listing_usage_system_samples", {
            p_caller: user.id,
            p_hours: SYSTEM_WINDOW_HOURS,
            p_bucket_minutes: SYSTEM_BUCKET_MINUTES,
          }),
          "system_health_snapshot_failed",
        ),
      ),
    ]);

    const dailyHeatmap = readSettled(
      heatmapResult,
      "daily_activity",
      { timezone: "Asia/Shanghai", window_days: DAILY_HEATMAP_DAYS, days: [] },
      partialErrors,
    );
    const rawTaskAudits = readSettled(auditsResult, "task_audits", [], partialErrors);
    const diagnostics = readSettled(diagnosticsResult, "diagnostics", [], partialErrors);
    const systemSamples = readSettled(systemResult, "system_health", [], partialErrors);

    const snapshot = snapshotComponent.value;
    const payload = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
      ? { ...snapshot }
      : {};

    const normalizedTaskAudits = normalizeTaskAudits(rawTaskAudits ?? []);

    payload.query_architecture = "usage_monitor_read_model_v2";
    payload.daily_activity = dailyHeatmap;
    payload.task_audits = normalizedTaskAudits;
    payload.task_audit_raw_count = Array.isArray(rawTaskAudits) ? rawTaskAudits.length : 0;
    payload.task_audit_limit = TASK_AUDIT_LIMIT;
    payload.task_metric_basis = "database_hourly_independent_product_audits";
    payload.task_metric_window_hours = SYSTEM_WINDOW_HOURS;
    payload.task_metric_raw_count = 0;
    payload.task_metric_limit = 0;
    payload.diagnostic_reports = diagnostics ?? [];
    payload.diagnostic_limit = DIAGNOSTIC_LIMIT;
    payload.system_samples = systemSamples ?? [];
    payload.system_sample_limit = Math.ceil((SYSTEM_WINDOW_HOURS * 60) / SYSTEM_BUCKET_MINUTES);
    payload.system_sample_limit_scope = "per_device";
    payload.system_sample_basis = "latest_sample_per_time_bucket";
    payload.system_bucket_minutes = SYSTEM_BUCKET_MINUTES;
    payload.system_window_hours = SYSTEM_WINDOW_HOURS;
    payload.partial_errors = partialErrors;
    payload.task_audit_scope = {
      includes: [
        "one independent audit per supplier product link",
        "per-product 24h success/failure chart counts",
        "365-day daily activity heatmap",
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
      excludes: [
        "API keys",
        "access / refresh tokens",
        "passwords",
        "cookies",
        "authorization secrets",
        "raw customer file binaries",
      ],
    };

    return json(req, payload);
  } catch {
    return json(req, { error: "server_error" }, 500);
  }
});
