from __future__ import annotations

from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.question_matcher import AMBIGUOUS, MATCHED, UNMATCHED, match_questions_to_fields


def _catalog(*questions: str) -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number=str(index), question=value)
            for index, value in enumerate(questions, start=1)
        ],
    )


def test_exact_normalized_question_matches_attribute_key():
    fields = [
        {"attribute_key": "model_number", "label": "Model Number", "section_heading": "Product Description"}
    ]
    audit = match_questions_to_fields(_catalog("Model Number"), fields)

    assert audit.matched_count == 1
    assert audit.matches[0].status == MATCHED
    assert audit.matches[0].semantic_field is fields[0]


def test_no_fuzzy_guess_for_similar_but_not_exact_label():
    fields = [
        {"attribute_key": "image_resolution", "label": "Image Resolution", "section_heading": "Additional Description"}
    ]
    audit = match_questions_to_fields(_catalog("Video Resolution"), fields)

    assert audit.matches[0].status == UNMATCHED
    assert audit.unmatched_fields == fields


def test_explicit_alias_can_bridge_known_equivalent_names():
    fields = [
        {"attribute_key": "image_resolution", "label": "Image Resolution", "section_heading": "Additional Description"}
    ]
    audit = match_questions_to_fields(
        _catalog("Video Resolution"),
        fields,
        aliases={"videoresolution": ("Image Resolution",)},
    )

    assert audit.matches[0].status == MATCHED
    assert audit.matches[0].match_basis == "explicit-alias"


def test_duplicate_live_labels_fail_as_ambiguous():
    fields = [
        {"attribute_key": "foo", "label": "Camera Type"},
        {"attribute_key": "bar", "label": "Camera Type"},
    ]
    audit = match_questions_to_fields(_catalog("Camera Type"), fields)

    assert audit.matches[0].status == AMBIGUOUS
    assert audit.matched_count == 0
