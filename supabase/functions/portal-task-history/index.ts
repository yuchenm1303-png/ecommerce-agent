import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = new Set(["https://smirel.com", "https://www.smirel.com"]);
const DEFAULT_PAGE_SIZE = 120;
const MAX_PAGE_SIZE = 120;
const SUMMARY_COLUMNS = "id,user_id,device_id,app_version,task_kind,phase,status,review_required,review_reason,product_url,error_text,started_at,completed_at,updated_at,created_at";

type JsonObject = Record<string, unknown>;
type ViewerScope = {
  mode: "owner" | "tenant";
  userIds: string[];
  tenantId: string;
  tenantName: string;
  tenantSlug: string;
};

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

function pageSize(value: unknown): number {
  const requested = Math.floor(numberValue(value) || DEFAULT_PAGE_SIZE);
  return Math.min(MAX_PAGE_SIZE, Math.max(1, requested));
}

function executionPhase(value: unknown): boolean {
  return textValue(value).toLowerCase().endsWith("_execute");
}

function monitorAudit(rowValue: unknown): JsonObject {
  const row = objectValue(rowValue);
  const storedStatus = textValue(row.status).toLowerCase();
  const hardFailure = storedStatus === "failed" || storedStatus === "cancelled";
  const reviewRequired = executionPhase(row.phase) && !hardFailure && (
    storedStatus === "review" || row.review_required === true
  );
  if (!reviewRequired) return { ...row };

  return {
    ...row,
    status: "completed",
    review_required: true,
    review_reason: textValue(row.review_reason) || textValue(row.error_text),
    error_text: "",
  };
}

function taskSummary(rowValue: unknown): JsonObject {
  const row = monitorAudit(rowValue);
  return {
    ...row,
    source_audit_id: textValue(row.id),
    input_data: {},
    result_data: {},
    summary_only: true,
  };
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

async function resolveViewerScope(
  admin: ReturnType<typeof createClient>,
  userId: string,
): Promise<ViewerScope> {
  const { data: access, error: accessError } = await admin
    .from("download_portal_users")
    .select("enabled,is_admin")
    .eq("user_id", userId)
    .maybeSingle();
  if (accessError) throw new Error("access_check_failed");
  if (access?.enabled && access?.is_admin) {
    return { mode: "owner", userIds: [], tenantId: "", tenantName: "", tenantSlug: "" };
  }

  const { data, error } = await admin.rpc("get_listing_monitor_scope", { p_caller: userId });
  if (error) throw new Error("tenant_scope_failed");
  const rows = Array.isArray(data) ? data.map(objectValue) : [];
  if (!rows.length) throw new Error("not_authorized");

  const first = rows[0];
  const userIds = [...new Set(rows.map((row) => textValue(row.user_id)).filter(Boolean))];
  if (!userIds.length) throw new Error("not_authorized");
  return {
    mode: "tenant",
    userIds,
    tenantId: textValue(first.tenant_id),
    tenantName: textValue(first.tenant_name) || "My Workspace",
    tenantSlug: textValue(first.tenant_slug) || "self",
  };
}

function applyViewerScope(query: any, scope: ViewerScope): any {
  return scope.mode === "tenant" ? query.in("user_id", scope.userIds) : query;
}

async function resolveAnchor(
  admin: ReturnType<typeof createClient>,
  scope: ViewerScope,
  auditId: string,
): Promise<{ id: string; updated_at: string } | null> {
  if (!auditId) return null;
  let query = admin.from("listing_task_audits")
    .select("id,updated_at")
    .eq("id", auditId);
  query = applyViewerScope(query, scope);
  const { data, error } = await query.maybeSingle();
  if (error) throw new Error("task_history_anchor_failed");
  if (!data) return null;
  const row = objectValue(data);
  const id = textValue(row.id);
  const updatedAt = textValue(row.updated_at);
  if (!id || !updatedAt) return null;
  return { id, updated_at: updatedAt };
}

async function queryPage(
  admin: ReturnType<typeof createClient>,
  scope: ViewerScope,
  limit: number,
  anchor: { id: string; updated_at: string } | null,
): Promise<JsonObject[]> {
  const fetchCount = limit + 1;
  if (!anchor) {
    let query = admin.from("listing_task_audits")
      .select(SUMMARY_COLUMNS)
      .order("updated_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(fetchCount);
    query = applyViewerScope(query, scope);
    const { data, error } = await query;
    if (error) throw new Error("task_history_page_failed");
    return Array.isArray(data) ? data.map(objectValue) : [];
  }

  let sameStampQuery = admin.from("listing_task_audits")
    .select(SUMMARY_COLUMNS)
    .eq("updated_at", anchor.updated_at)
    .lt("id", anchor.id)
    .order("id", { ascending: false })
    .limit(fetchCount);
  sameStampQuery = applyViewerScope(sameStampQuery, scope);
  const { data: sameStampData, error: sameStampError } = await sameStampQuery;
  if (sameStampError) throw new Error("task_history_page_failed");
  const sameStampRows = Array.isArray(sameStampData) ? sameStampData.map(objectValue) : [];
  if (sameStampRows.length >= fetchCount) return sameStampRows;

  const remaining = fetchCount - sameStampRows.length;
  let olderQuery = admin.from("listing_task_audits")
    .select(SUMMARY_COLUMNS)
    .lt("updated_at", anchor.updated_at)
    .order("updated_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(remaining);
  olderQuery = applyViewerScope(olderQuery, scope);
  const { data: olderData, error: olderError } = await olderQuery;
  if (olderError) throw new Error("task_history_page_failed");
  const olderRows = Array.isArray(olderData) ? olderData.map(objectValue) : [];
  return [...sameStampRows, ...olderRows];
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(req) });
  if (req.method !== "POST") return json(req, { error: "method_not_allowed" }, 405);

  try {
    const { userClient, admin } = clients(req);
    const { data: userData, error: userError } = await userClient.auth.getUser();
    const user = userData?.user;
    if (userError || !user) return json(req, { error: "invalid_auth" }, 401);

    let scope: ViewerScope;
    try {
      scope = await resolveViewerScope(admin, user.id);
    } catch (error) {
      const code = error instanceof Error ? error.message : "not_authorized";
      if (code === "not_authorized") return json(req, { error: code }, 403);
      return json(req, { error: code }, 503);
    }

    let body: JsonObject = {};
    try { body = objectValue(await req.json()); } catch { body = {}; }
    const limit = pageSize(body.limit);
    const beforeAuditId = textValue(body.before_audit_id);
    const anchor = await resolveAnchor(admin, scope, beforeAuditId);
    if (beforeAuditId && !anchor) return json(req, { error: "task_history_anchor_not_found" }, 404);

    const rows = await queryPage(admin, scope, limit, anchor);
    const hasMore = rows.length > limit;
    const pageRows = rows.slice(0, limit);
    const summaries = pageRows.map(taskSummary);
    const last = pageRows.length ? pageRows[pageRows.length - 1] : null;

    return json(req, {
      query_architecture: "usage_monitor_task_history_keyset_v1",
      viewer_scope: scope.mode,
      tenant: scope.mode === "tenant"
        ? { id: scope.tenantId || null, name: scope.tenantName, slug: scope.tenantSlug }
        : null,
      task_audits: summaries,
      page_size: limit,
      has_more: hasMore,
      next_before_audit_id: hasMore && last ? textValue(last.id) : null,
    });
  } catch (error) {
    const code = error instanceof Error ? error.message : "server_error";
    if (code === "task_history_anchor_failed" || code === "task_history_page_failed") {
      return json(req, { error: code }, 503);
    }
    return json(req, { error: "server_error" }, 500);
  }
});
