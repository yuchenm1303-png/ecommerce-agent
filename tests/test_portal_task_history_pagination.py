from pathlib import Path


SOURCE = Path("supabase/functions/portal-task-history/index.ts")


def test_task_history_keeps_lightweight_bounded_pages():
    text = SOURCE.read_text(encoding="utf-8")
    assert 'const DEFAULT_PAGE_SIZE = 120;' in text
    assert 'const MAX_PAGE_SIZE = 120;' in text
    assert 'const fetchCount = limit + 1;' in text
    assert 'input_data: {}' in text
    assert 'result_data: {}' in text
    assert 'summary_only: true' in text


def test_task_history_uses_stable_keyset_boundary_and_scope_checks():
    text = SOURCE.read_text(encoding="utf-8")
    assert '.order("updated_at", { ascending: false })' in text
    assert '.order("id", { ascending: false })' in text
    assert '.eq("updated_at", anchor.updated_at)' in text
    assert '.lt("id", anchor.id)' in text
    assert '.lt("updated_at", anchor.updated_at)' in text
    assert 'before_audit_id' in text
    assert 'applyViewerScope' in text
    assert 'next_before_audit_id' in text
    assert 'has_more: hasMore' in text
