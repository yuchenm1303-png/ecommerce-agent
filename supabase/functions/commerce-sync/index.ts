import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const EVENT_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;

function corsHeaders(): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function adminClient() {
  const url = Deno.env.get("SUPABASE_URL") || "";
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!url || !serviceRole) throw new Error("server_config");
  return createClient(url, serviceRole, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function bearer(req: Request): string {
  const header = req.headers.get("Authorization") || "";
  return header.startsWith("Bearer ") ? header.slice(7).trim() : "";
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown, limit: number): string {
  return String(value || "").trim().slice(0, limit);
}

async function resolveWorkspace(
  admin: ReturnType<typeof createClient>,
  userId: string,
  requestedWorkspace: string,
): Promise<{ workspaceId?: string; error?: string; status?: number }> {
  let query = admin
    .from("listing_monitor_tenant_members")
    .select("tenant_id")
    .eq("user_id", userId);
  if (requestedWorkspace) query = query.eq("tenant_id", requestedWorkspace);

  const { data, error } = await query.limit(3);
  if (error) return { error: "membership_check_failed", status: 503 };

  const ids = Array.from(new Set(
    (data || [])
      .map((row) => text((row as Record<string, unknown>).tenant_id, 64))
      .filter(Boolean),
  ));
  if (requestedWorkspace) {
    if (ids.length !== 1) return { error: "workspace_forbidden", status: 403 };
    return { workspaceId: requestedWorkspace };
  }
  if (ids.length === 0) return { error: "workspace_forbidden", status: 403 };
  if (ids.length !== 1) return { error: "workspace_required", status: 409 };
  return { workspaceId: ids[0] };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders() });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const token = bearer(req);
  if (!token) return json({ error: "missing_auth" }, 401);

  let body: Record<string, unknown>;
  try {
    body = objectValue(await req.json()) || {};
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const action = text(body.action, 64);
  const eventId = text(body.event_id, 200);
  const occurredAt = text(body.occurred_at, 80);
  const requestedWorkspace = text(body.workspace_id, 64);
  const payload = objectValue(body.payload);
  if (action !== "listing_success") return json({ error: "invalid_action" }, 400);
  if (!EVENT_RE.test(eventId)) return json({ error: "invalid_event_id" }, 400);
  if (!occurredAt || Number.isNaN(Date.parse(occurredAt))) {
    return json({ error: "invalid_occurred_at" }, 400);
  }
  if (requestedWorkspace && !UUID_RE.test(requestedWorkspace)) {
    return json({ error: "invalid_workspace_id" }, 400);
  }
  if (!payload) return json({ error: "invalid_payload" }, 400);

  const product = objectValue(payload.product);
  const source = objectValue(payload.source);
  const account = objectValue(payload.channel_account);
  const listing = objectValue(payload.listing);
  if (!product || !source || !account || !listing) {
    return json({ error: "invalid_payload" }, 400);
  }
  if (!text(product.product_id, 200) || !text(product.variant_id, 200) || !text(product.display_name, 500)) {
    return json({ error: "product_identity_required" }, 400);
  }
  if (!text(source.source_product_id, 200) || !text(source.request_identity, 4000) || !text(source.supplier_url, 4000)) {
    return json({ error: "source_identity_required" }, 400);
  }
  if (!text(account.channel, 32) || !text(account.channel_account_id, 200) || !text(account.label, 120)) {
    return json({ error: "channel_account_required" }, 400);
  }
  // Browser target/request ids are deliberately not accepted as listing identity.
  // The gateway only records an active ChannelListing after the marketplace has
  // produced a real external listing id.
  if (!text(listing.listing_id, 200) || !text(listing.external_listing_id, 300)) {
    return json({ error: "external_listing_id_required" }, 400);
  }

  const admin = adminClient();
  const { data: userData, error: userError } = await admin.auth.getUser(token);
  const userId = text(userData?.user?.id, 64);
  if (userError || !userId) return json({ error: "invalid_auth" }, 401);

  const scope = await resolveWorkspace(admin, userId, requestedWorkspace);
  if (!scope.workspaceId) return json({ error: scope.error || "workspace_forbidden" }, scope.status || 403);

  // This is intentionally the only Commerce persistence call in the Edge
  // function. Product/source/account/listing/event writes commit atomically in
  // PostgreSQL or roll back together.
  const { data, error } = await admin.rpc("commerce_sync_listing_success_v1", {
    p_workspace_id: scope.workspaceId,
    p_event_id: eventId,
    p_occurred_at: occurredAt,
    p_payload: payload,
  });
  if (error) {
    console.error("commerce_sync_rpc_failed", error.message);
    return json({ error: "commerce_sync_failed" }, 503);
  }

  const result = objectValue(data) || {};
  if (result.accepted !== true) {
    const code = text(result.error, 120) || "commerce_sync_rejected";
    const status = code.endsWith("_conflict") ? 409 : 400;
    return json({ ...result, error: code }, status);
  }
  return json({
    ...result,
    accepted: true,
    action,
    event_id: eventId,
    workspace_id: scope.workspaceId,
  });
});
