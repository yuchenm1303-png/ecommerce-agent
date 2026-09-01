from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDGE_FUNCTIONS = (
    ROOT / "supabase" / "functions" / "portal-usage-admin" / "index.ts",
    ROOT / "supabase" / "functions" / "portal-usage-tenant" / "index.ts",
)
MIGRATION = ROOT / "supabase" / "migrations" / "20260901073500_listing_monitor_review_success_semantics.sql"


def test_monitor_edges_project_execution_review_as_completed_with_review_metadata() -> None:
    for path in EDGE_FUNCTIONS:
        source = path.read_text(encoding="utf-8")

        assert 'if (status === "DONE" || status === "REVIEW") return "completed";' in source
        assert 'storedStatus === "review" || jobStatus === "REVIEW" ||' in source
        assert 'audit.review_required === true || result.review_required === true' in source
        assert 'status: "completed"' in source
        assert 'review_required: true' in source
        assert 'review_reason: reviewReason' in source
        assert 'error_text: ""' in source
        assert 'const hardFailure = storedStatus === "failed" || storedStatus === "cancelled"' in source

        # Lightweight monitor lists must receive the orthogonal review metadata
        # without loading the full result payload.
        assert "phase,status,review_required,review_reason,product_url" in source


def test_monitor_edges_keep_prepare_review_distinct_from_execution_success() -> None:
    for path in EDGE_FUNCTIONS:
        source = path.read_text(encoding="utf-8")
        job_status = source.split("function jobAuditStatus", 1)[1].split("function monitorAudit", 1)[0]

        assert 'if (textValue(phase).toLowerCase() === "batch_execute")' in job_status
        assert 'if (status === "DONE" || status === "REVIEW") return "completed";' in job_status
        assert 'if (status === "REVIEW") return "review";' in job_status


def test_database_boundary_persists_review_orthogonally_and_normalizes_old_rows() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "add column if not exists review_required boolean not null default false" in sql
    assert "add column if not exists review_reason text not null default ''" in sql
    assert "normalize_listing_task_audit_review_semantics" in sql
    assert "before insert or update of phase, status, result_data, error_text, review_required, review_reason" in sql
    assert "new.status := 'completed';" in sql
    assert "new.error_text := '';" in sql
    assert "new.review_required := v_review;" in sql
    assert "new.review_reason := v_reason;" in sql
    assert "v_hard_failure" in sql

    # Existing rows are re-projected through the same trigger rather than being
    # maintained by a separate historical special case.
    assert "set result_data = result_data" in sql


def test_read_model_counts_legacy_execute_review_as_success_but_prepare_review_as_review() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    execute_case = sql.split("when lower(a.phase) = 'batch_execute' then", 1)[1].split("else", 1)[0]
    prepare_case = sql.split("when lower(a.phase) = 'batch_execute' then", 1)[1].split("else", 1)[1]

    assert "when 'REVIEW' then 'completed'" in execute_case
    assert "when 'REVIEW' then 'review'" in prepare_case
    assert "'2026-08-20 00:00:00+00'::timestamptz" in sql
    assert "refresh_listing_usage_read_model" in sql
