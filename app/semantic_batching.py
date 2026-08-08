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


MAX_REPAIR_ATTEMPTS = 2


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
    per_source: bool = True,
) -> SemanticBatchRunResult:
    """Run bounded semantic extraction while preserving fail-closed behavior.

    Invalid model packets are isolated to their (batch, source) pass: none of
    their facts enter the evidence bundle, while unrelated validated passes may
    still complete. Product identity disagreement is different: it means we may
    be looking at a different product, so IdentityMismatchError always aborts
    the entire run. Provider/API failures also surface immediately rather than
    being hidden as a content-validation failure.

    ``per_source`` (default True) runs one extraction pass per grounded source
    instead of letting the model freely choose between sources. This is a
    deliberate evidence-recall improvement: candidate facts that each source
    explicitly supports are all returned to the resolver, which then performs
    conflict detection. The model is never asked to pre-select a final answer
    for the resolver.
    """

    batches = build_semantic_question_batches(catalog, batch_size=batch_size)
    all_facts = []
    warnings: list[str] = []
    failures: list[SemanticBatchFailure] = []
    observed_identity = expected_identity
    completed = 0

    for batch in batches:
        source_pool = grounding.sources if per_source else [grounding]
        batch_facts = []
        batch_warnings: list[str] = []
        batch_success = False

        for pool_entry in source_pool:
            pass_grounding = (
                GroundingCatalog(sources=[pool_entry]) if per_source else pool_entry
            )
            request = build_grounded_semantic_request(
                batch.catalog,
                pass_grounding,
                identity=expected_identity,
            )
            request["batch_id"] = batch.batch_id
            request["batch_question_numbers"] = list(batch.question_numbers)
            pass_label = pool_entry.source_id if per_source else "all-sources"

            def attempt() -> EvidencePacket:
                raw = provider.extract_json(request)
                if not isinstance(raw, dict):
                    raise SemanticGroundingError(
                        f"semantic provider {provider.name!r} 未返回 JSON object。"
                    )
                return validate_grounded_semantic_packet(
                    raw,
                    batch.catalog,
                    pass_grounding,
                    expected_identity=expected_identity,
                )

            # Models are not always compliant on the first pass (missing
            # evidence_text, empty values, paraphrased excerpts). A bounded
            # repair loop re-prompts with the exact rejection reason. Repaired
            # output still has to pass the same strict grounding validation, so
            # this never weakens the fail-closed trust boundary.
            packet: EvidencePacket | None = None
            failure: EvidenceContractError | None = None
            try:
                packet = attempt()
            except IdentityMismatchError:
                raise
            except EvidenceContractError as exc:
                failure = exc
                for _ in range(MAX_REPAIR_ATTEMPTS):
                    request["validation_error"] = (
                        "Your previous output was rejected by strict validation: "
                        + str(exc)
                        + " Return a complete corrected JSON object satisfying every rule. "
                        "Remove any fact you cannot fully ground instead of leaving empty fields."
                    )
                    try:
                        packet = attempt()
                        failure = None
                        break
                    except IdentityMismatchError:
                        raise
                    except EvidenceContractError as retry_exc:
                        exc = retry_exc
                        failure = retry_exc

            if packet is None:
                if not continue_on_batch_error:
                    assert failure is not None
                    raise failure
                failures.append(
                    SemanticBatchFailure(
                        batch_id=batch.batch_id,
                        question_numbers=batch.question_numbers,
                        error=f"[source={pass_label}] {failure}",
                    )
                )
                continue

            observed_identity = _merge_observed_identity(observed_identity, packet.identity)
            batch_success = True
            batch_facts.extend(packet.facts)
            batch_warnings.extend(
                f"{batch.batch_id}[{pass_label}]: {item}" for item in packet.warnings
            )

        if not batch_success:
            continue
        completed += 1
        all_facts.extend(batch_facts)
        warnings.extend(batch_warnings)

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
