from __future__ import annotations

import inspect

from gui.batch_model import BatchJob
from gui.listing_offer_support import ListingOfferSupport


def test_batch_job_roundtrip_persists_listing_intent() -> None:
    job = BatchJob(
        job_id="JOB-001",
        product_url="https://supplier.example/Product/ABC?sku=Red",
        listing_intent="红色 2件套",
    )

    restored = BatchJob.from_dict(job.as_dict())
    assert restored.listing_intent == "红色 2件套"


def test_legacy_batch_job_without_listing_intent_remains_loadable() -> None:
    restored = BatchJob.from_dict(
        {
            "job_id": "JOB-001",
            "product_url": "https://supplier.example/Product/ABC?sku=Red",
        }
    )
    assert restored.listing_intent == ""


def test_batch_offer_mapping_uses_exact_supplier_request_identity() -> None:
    source = inspect.getsource(ListingOfferSupport._batch_intent_by_url)
    assert "supplier_request_identity(url)" in source
    assert "url.casefold()" not in source


def test_batch_process_handoff_reads_persisted_job_intent() -> None:
    job_source = inspect.getsource(ListingOfferSupport._job_intent)
    handoff_source = inspect.getsource(ListingOfferSupport._install_process_handoff)

    assert 'getattr(job, "listing_intent", "")' in job_source
    assert "job.listing_intent = intent" in handoff_source
    assert "_controller._persist_emit(immediate=True)" in handoff_source
