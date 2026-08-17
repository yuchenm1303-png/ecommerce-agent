import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ACCESS_TABLE = "download_portal_users";
const DEVICE_TABLE = "download_portal_devices";
const SESSION_TABLE = "listing_usage_sessions";
const EVENT_TABLE = "listing_usage_events";
const DIAGNOSTIC_TABLE = "listing_diagnostic_reports";
const AUDIT_TABLE = "listing_task_audits";
const DEVICE_RE = /^[0-9a-f]{32,128}$/i;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TOKEN_RE = /^[A-Za-z0-9_-]{32,256}$/;
const EVENTS = new Set(["listing_prepare", "listing_execute", "batch_prepare", "batch_execute"]);
const OUTCOMES = new Set(["started", "completed", "failed"]);
const AUDIT_KINDS = new Set(["single", "batch"]);
const AUDIT_STATUSES = new Set(["running", "completed", "failed", "cancelled", "review", "ready"]);
const MAX_DIAGNOSTIC_BYTES = 80_000;
const MAX_AUDIT_BYTES = 260_000;
const SECRET_KEY_RE = /(^|_)(api[_-]?key|token|secret|password|authorization|cookie|refresh[_-]?token|access[_-]?token)($|_)/i;

function headers(): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: headers() });
}

function adminClient() {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!url || !serviceRole) throw new Error("server_config");
  return createClient(url, serviceRole, { auth: { persistSession: false, autoRefreshToken: false } });
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((part) => part.toString(16).padStart(2, "0")).join("");
}

function diagnosticCode(): string {
  const stamp = Date.now().toString(36).toUpperCase();
  const random = crypto.randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase();
  return `LS-${stamp}-${random}`;
}

function redactSecrets(value: unknown, depth = 0): unknown {
  if (depth > 10) return "[TRUNCATED]";
  if (Array.isArray(value)) return value.slice(0, 500).map((item) => redactSecrets(item, depth + 1));
  if (!value || typeof value !== "object") {
    if (typeof value === "string") return value.slice(0, 32_000);
    return value;
  }
  const source = value as Record<string, unknown>;
  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(source).slice(0, 500)) {
    output[key.slice(0, 160)] = SECRET_KEY_RE.test(key) ? "[REDACTED]" : redactSecrets(item, depth + 1);
  }
  return output;
}

function safeIso(value: unknown, fallback: string | null): string | null {
  const text = String(value || "").trim();
  if (!text) return fallback;
  const timestamp = Date.parse(text);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : fallback;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: headers() });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { return json({ error: "invalid_json" }, 400); }

  const action = String(body.action || "heartbeat").trim();
  const userId = String(body.user_id || "").trim().toLowerCase();
  const deviceId = String(body.device_id || "").trim().toLowerCase();
  const sessionId = String(body.session_id || "").trim().toLowerCase();
  const token = String(body.telemetry_token || "").trim();
  const appVersion = String(body.app_version || "").trim().slice(0, 64);
  const eventType = String(body.event_type || "").trim();
  const outcome = String(body.outcome || "").trim();

  if (!["session_start", "heartbeat", "event", "session_end", "diagnostic", "task_audit"].includes(action)) {
    return json({ error: "invalid_action" }, 400);
  }
  if (!UUID_RE.test(userId)) return json({ error: "invalid_identity" }, 400);
  if (action !== "diagnostic" && !UUID_RE.test(sessionId)) return json({ error: "invalid_identity" }, 400);
  if (!DEVICE_RE.test(deviceId) || !TOKEN_RE.test(token)) return json({ error: "invalid_device_auth" }, 400);
  if (action === "event" && (!EVENTS.has(eventType) || !OUTCOMES.has(outcome))) {
    return json({ error: "invalid_event" }, 400);
  }

  const admin = adminClient();
  const nowIso = new Date().toISOString();

  const { data: access, error: accessError } = await admin
    .from(ACCESS_TABLE)
    .select("enabled, expires_at")
    .eq("user_id", userId)
    .maybeSingle();
  if (accessError) return json({ error: "access_check_failed" }, 503);
  if (!access?.enabled) return json({ error: "not_authorized" }, 403);
  if (access.expires_at && Date.parse(access.expires_at) <= Date.now()) return json({ error: "access_expired" }, 403);

  const { data: device, error: deviceError } = await admin
    .from(DEVICE_TABLE)
    .select("enabled, revoked_at, telemetry_token_hash")
    .eq("user_id", userId)
    .eq("device_id", deviceId)
    .maybeSingle();
  if (deviceError) return json({ error: "device_check_failed" }, 503);
  if (!device || !device.enabled || device.revoked_at) return json({ error: "device_not_authorized" }, 403);
  if (!device.telemetry_token_hash || await sha256Hex(token) !== device.telemetry_token_hash) {
    return json({ error: "invalid_telemetry_token" }, 401);
  }

  const { error: deviceTouchError } = await admin
    .from(DEVICE_TABLE)
    .update({ last_seen_at: nowIso, app_version: appVersion, updated_at: nowIso })
    .eq("user_id", userId)
    .eq("device_id", deviceId);
  if (deviceTouchError) return json({ error: "device_touch_failed" }, 503);

  if (action === "diagnostic") {
    const diagnostic = body.diagnostic;
    if (!diagnostic || typeof diagnostic !== "object" || Array.isArray(diagnostic)) {
      return json({ error: "invalid_diagnostic" }, 400);
    }
    const encoded = JSON.stringify(diagnostic);
    if (new TextEncoder().encode(encoded).byteLength > MAX_DIAGNOSTIC_BYTES) {
      return json({ error: "diagnostic_too_large" }, 413);
    }
    const report = diagnostic as Record<string, unknown>;
    const crashId = String(report.crash_id || "").trim().slice(0, 160);
    const startupStage = String(report.last_stage || "").trim().slice(0, 160);
    if (crashId.length < 8) return json({ error: "invalid_crash_id" }, 400);

    const reportCode = diagnosticCode();
    const { error } = await admin.from(DIAGNOSTIC_TABLE).insert({
      report_code: reportCode,
      user_id: userId,
      device_id: deviceId,
      app_version: appVersion,
      crash_id: crashId,
      startup_stage: startupStage,
      report,
      created_at: nowIso,
    });
    if (error) return json({ error: "diagnostic_write_failed" }, 503);
    return json({ accepted: true, action, report_code: reportCode, server_time: nowIso });
  }

  const { data: existingSession, error: sessionError } = await admin
    .from(SESSION_TABLE)
    .select("id, user_id, device_id")
    .eq("id", sessionId)
    .maybeSingle();
  if (sessionError) return json({ error: "session_check_failed" }, 503);
  if (existingSession && (existingSession.user_id !== userId || existingSession.device_id !== deviceId)) {
    return json({ error: "session_owner_mismatch" }, 403);
  }

  if (!existingSession) {
    const { error } = await admin.from(SESSION_TABLE).insert({
      id: sessionId,
      user_id: userId,
      device_id: deviceId,
      app_version: appVersion,
      started_at: nowIso,
      last_seen_at: nowIso,
      ended_at: action === "session_end" ? nowIso : null,
      created_at: nowIso,
    });
    if (error) return json({ error: "session_create_failed" }, 503);
  } else {
    const patch: Record<string, unknown> = { last_seen_at: nowIso, app_version: appVersion };
    if (action === "session_start") patch.ended_at = null;
    if (action === "session_end") patch.ended_at = nowIso;
    const { error } = await admin.from(SESSION_TABLE).update(patch).eq("id", sessionId).eq("user_id", userId);
    if (error) return json({ error: "session_update_failed" }, 503);
  }

  if (action === "event") {
    const { error } = await admin.from(EVENT_TABLE).insert({
      user_id: userId,
      session_id: sessionId,
      device_id: deviceId,
      event_type: eventType,
      outcome,
      app_version: appVersion,
      occurred_at: nowIso,
      created_at: nowIso,
    });
    if (error) return json({ error: "event_write_failed" }, 503);
  }

  if (action === "task_audit") {
    const rawAudit = body.audit;
    if (!rawAudit || typeof rawAudit !== "object" || Array.isArray(rawAudit)) {
      return json({ error: "invalid_task_audit" }, 400);
    }
    const audit = redactSecrets(rawAudit) as Record<string, unknown>;
    const encoded = JSON.stringify(audit);
    if (new TextEncoder().encode(encoded).byteLength > MAX_AUDIT_BYTES) {
      return json({ error: "task_audit_too_large" }, 413);
    }

    const auditId = String(audit.id || "").trim().toLowerCase();
    const taskKind = String(audit.task_kind || "").trim().toLowerCase();
    const phase = String(audit.phase || "").trim().slice(0, 80);
    const status = String(audit.status || "running").trim().toLowerCase();
    const productUrl = String(audit.product_url || "").trim().slice(0, 4096);
    const errorText = String(audit.error_text || "").trim().slice(0, 12_000);
    const inputData = audit.input_data && typeof audit.input_data === "object" && !Array.isArray(audit.input_data)
      ? audit.input_data
      : {};
    const resultData = audit.result_data && typeof audit.result_data === "object" && !Array.isArray(audit.result_data)
      ? audit.result_data
      : {};
    if (!UUID_RE.test(auditId) || !AUDIT_KINDS.has(taskKind) || !AUDIT_STATUSES.has(status)) {
      return json({ error: "invalid_task_audit_contract" }, 400);
    }

    const { data: existingAudit, error: auditCheckError } = await admin
      .from(AUDIT_TABLE)
      .select("id, user_id, device_id, created_at")
      .eq("id", auditId)
      .maybeSingle();
    if (auditCheckError) return json({ error: "task_audit_check_failed" }, 503);
    if (existingAudit && (existingAudit.user_id !== userId || existingAudit.device_id !== deviceId)) {
      return json({ error: "task_audit_owner_mismatch" }, 403);
    }

    const record = {
      user_id: userId,
      session_id: sessionId,
      device_id: deviceId,
      app_version: appVersion,
      task_kind: taskKind,
      phase,
      status,
      product_url: productUrl,
      input_data: inputData,
      result_data: resultData,
      error_text: errorText,
      started_at: safeIso(audit.started_at, existingAudit ? null : nowIso) || nowIso,
      completed_at: safeIso(audit.completed_at, null),
      updated_at: nowIso,
    };

    if (existingAudit) {
      const { error } = await admin.from(AUDIT_TABLE).update(record).eq("id", auditId).eq("user_id", userId);
      if (error) return json({ error: "task_audit_update_failed" }, 503);
    } else {
      const { error } = await admin.from(AUDIT_TABLE).insert({ id: auditId, ...record, created_at: nowIso });
      if (error) return json({ error: "task_audit_create_failed" }, 503);
    }
  }

  return json({ accepted: true, action, server_time: nowIso });
});
