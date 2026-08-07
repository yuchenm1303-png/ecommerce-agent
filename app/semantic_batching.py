from __future__ import annotations

from dataclasses import dataclass, field

from .evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    IdentityMismatchError,
    ProductIdentity,
    assert_identity_compatible,
)
from .evidence_validation import is_business_question
from .qa_catalog import QuestionCatalog, QuestionRecord
from .semantic_extraction import (
    SemanticExtractionProvider,
    SemanticGroundingError,
    build_grounded_semantic_request,
    validate_grounded_semantic_packet,
)
from .semantic_grounding import GroundingCatalog


@dataclass(slots=True, frozen=True)
class SemanticQuestionBatch:
    batch_id: str
    catalog: QuestionCatalog
    question_numbers: tuple[str, ...]


@dataclass(slots=True)
class SemanticBatchFailure:
    batch_id: str
    question_numbers: tuple[str, ...]
    error: str


@dataclass(slots=True)
class SemanticBatchRunResult:
    packet: EvidencePacket
    completed_batches: int
    failures: list[SemanticBatchFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failed_batches(self) -> int:
        return len(self.failures)

    @property
    def partial(self) -> bool:
        return bool(self.failures)


def _subcatalog(catalog: QuestionCatalog, questions: list[QuestionRecord]) -> QuestionCatalog:
    return QuestionCatalog(
        source_path=catalog.source_path,
        sheet_name=catalog.sheet_name,
        header_row=catalog.header_row,
        questions=questions,
    )


def build_semantic_question_batches(
    catalog: QuestionCatalog,
    *,
    batch_size: int = 12,
    skip_answered: bool = True,
) -> list[SemanticQuestionBatch]:
    """Split only questions that are legally eligible for semantic extraction.

    Business/operational fields are excluded unconditionally. There is no flag
    that can turn this protection off: price, MOQ, fulfilment, shipping and
    similar seller settings must enter through structured/config/rule sources.
    """

    if batch_size < 1 or batch_size > 50:
        raise ValueError("batch_size 必须在 1..50。")

    pending = [
        question
        for question in catalog.questions
        if not (skip_answered and question.has_answer)
        and not is_business_question(question.question)
    ]

    batches: list[SemanticQuestionBatch] = []
    for offset in range(0, len(pending), batch_size):
        questions = pending[offset : offset + batch_size]
        batch_index = len(batches) + 1
        batches.append(
            SemanticQuestionBatch(
                batch_id=f"batch-{batch_index:03d}",
                catalog=_subcatalog(catalog, questions),
                question_numbers=tuple(item.number for item in questions),
            )
        )
    return batches


def _merge_observed_identity(current: ProductIdentity, observed: ProductIdentity) -> ProductIdentity:
    assert_identity_compatible(current, observed)
    assert_identity_compatible(observed, current)
    return ProductIdentity(
        sku=current.sku or observed.sku,
        model_number=current.model_number or observed.model_number,
        brand=current.brand or observed.brand,
    )


def run_grounded_semantic_batches(
    provider: SemanticExtractionProvider,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    batch_size: int = 12,
    continue_on_batch_error: bool = True,
) -> SemanticBatchRunResult:
    """Run bounded semantic extraction while preserving fail-closed behavior.

    Invalid model packets are isolated to their batch: none of their facts enter
    the evidence bundle, while unrelated validated batches may still complete.
    Product identity disagreement is different: it means we may be looking at a
    different product, so IdentityMismatchError always aborts the entire run.
    Provider/API failures also surface immediately rather than being hidden as a
    content-validation failure.
    """

    batches = build_semantic_question_batches(catalog, batch_size=batch_size)
    all_facts = []
    warnings: list[str] = []
    failures: list[SemanticBatchFailure] = []
    observed_identity = expected_identity
    completed = 0

    for batch in batches:
        request = build_grounded_semantic_request(
            batch.catalog,
            grounding,
            identity=expected_identity,
        )
        request["batch_id"] = batch.batch_id
        request["batch_question_numbers"] = list(batch.question_numbers)

        try:
            raw = provider.extract_json(request)
            if not isinstance(raw, dict):
                raise SemanticGroundingError(
                    f"semantic provider {provider.name!r} 未返回 JSON object。"
                )
            packet = validate_grounded_semantic_packet(
                raw,
                batch.catalog,
                grounding,
                expected_identity=expected_identity,
            )
            observed_identity = _merge_observed_identity(observed_identity, packet.identity)
        except IdentityMismatchError:
            raise
        except EvidenceContractError as exc:
            if not continue_on_batch_error:
                raise
            failures.append(
                SemanticBatchFailure(
                    batch_id=batch.batch_id,
                    question_numbers=batch.question_numbers,
                    error=str(exc),
                )
            )
            continue

        completed += 1
        all_facts.extend(packet.facts)
        warnings.extend(f"{batch.batch_id}: {item}" for item in packet.warnings)

    if failures:
        warnings.extend(
            f"{failure.batch_id} failed; questions remain blocked: "
            + ", ".join(failure.question_numbers)
            for failure in failures
        )

    return SemanticBatchRunResult(
        packet=EvidencePacket(
            identity=observed_identity,
            facts=all_facts,
            extractor=f"semantic-batched:{provider.name}",
            warnings=warnings,
        ),
        completed_batches=completed,
        failures=failures,
        warnings=warnings,
    )
