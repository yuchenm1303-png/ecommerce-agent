from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

from app.product_input import validate_product_input
from app.source_capture import _source_cache_key
from app.supplier_url_identity import supplier_request_identity
from app.providers.transient_retry import is_retryable_ai_error
from gui.task_failure_diagnostics import collect_workflow_failure_diagnostic


def test_supplier_identity_preserves_exact_request_semantics() -> None:
    clean = "HTTPS://DETAIL.1688.COM/offer/850845635717.html?spm=a&from=market#tab"
    assert supplier_request_identity(clean) == (
        "https://detail.1688.com/offer/850845635717.html?spm=a&from=market"
    )
    assert supplier_request_identity(clean) != supplier_request_identity(
        "https://detail.1688.com/offer/850845635717.html?spm=b&from=market"
    )


def test_source_cache_uses_exact_supplier_request_identity() -> None:
    base = "https://detail.1688.com/offer/850845635717.html"
    tracked = base + "?spm=a2615.2177701.autotrace-offerGeneral.1&from=market"
    assert _source_cache_key(base) != _source_cache_key(tracked)


def test_supplier_url_remains_primary_when_customer_files_supplement_it() -> None:
    url = "https://detail.1688.com/offer/850845635717.html?sku=black"
    assert validate_product_input(product_url=url, product_files=("customer-spec.pdf",)) == "supplier_url"
    assert validate_product_input(product_url=url) == "supplier_url"


def test_batch_runtime_keeps_lossless_fifo_preview_and_never_authorizes_qc() -> None:
    source = (Path(__file__).resolve().parents[1] / "gui" / "batch_runner.py").read_text(encoding="utf-8")
    assert "self._pending_log_preview: deque[str] = deque()" in source
    assert "journal.append(line)" in source
    assert "self.batch.send_to_qc = False" in source


def test_whole_request_wall_clock_deadline_is_terminal() -> None:
    assert is_retryable_ai_error(RuntimeError("AI whole-request wall-clock deadline exceeded: 30s")) is False
    assert is_retryable_ai_error(RuntimeError("socket timed out")) is True


def test_failure_event_fallback_preserves_traceback(tmp_path: Path) -> None:
    run_dir = tmp_path / "workflow"
    run_dir.mkdir()
    (run_dir / "workflow-diagnostics.jsonl").write_text(
        json.dumps(
            {
                "event": "FAILED",
                "stage": "source",
                "error_type": "RuntimeError",
                "error": "boom",
                "traceback": "Traceback (most recent call last):\nRuntimeError: boom",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    diagnostic = collect_workflow_failure_diagnostic(run_dir)
    assert diagnostic["truth_source"] == "workflow_diagnostics_fallback"
    assert "Traceback" in diagnostic["traceback"]
    assert diagnostic["timeline"][-1]["traceback"] == "[see failure_diagnostic.traceback]"


def test_stage_log_is_canonical_and_integrity_checked(tmp_path: Path) -> None:
    workflow = tmp_path / "jobs" / "JOB-1" / "workflow"
    diagnostics = workflow.parent / "diagnostics"
    workflow.mkdir(parents=True)
    diagnostics.mkdir()
    text = "before\nTraceback (most recent call last):\nRuntimeError: failed token=secret\n"
    (diagnostics / "prepare.log").write_text(text, encoding="utf-8")

    diagnostic = collect_workflow_failure_diagnostic(workflow, fallback_stage="解析字段")
    stage_log = diagnostic["stage_log"]
    encoded = "".join(stage_log["chunks"])
    decoded = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    data = decoded.encode("utf-8")

    assert diagnostic["schema"] == 4
    assert diagnostic["truth_source"] == "stage_log"
    assert diagnostic["diagnostic_sources"]["stage_log"] is True
    assert diagnostic["diagnostic_sources"]["process_log"] is True
    # Integrity metadata must describe the exact sanitized payload that is shipped,
    # regardless of the host OS newline representation used by Path.write_text().
    assert diagnostic["line_count"] == len(decoded.splitlines())
    assert diagnostic["byte_count"] == len(data)
    assert diagnostic["sha256"] == hashlib.sha256(data).hexdigest()
    assert stage_log["byte_count"] == len(data)
    assert stage_log["sha256"] == hashlib.sha256(data).hexdigest()
    assert "secret" not in json.dumps(diagnostic, ensure_ascii=False)
