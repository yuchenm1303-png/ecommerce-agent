from __future__ import annotations

from openpyxl import Workbook

from app.qa_catalog import load_question_catalog


def test_title_cell_questions_is_not_mistaken_for_header(tmp_path):
    path = tmp_path / "qa.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Vehicle Camera System"])
    sheet.append(["Questions"])
    sheet.append(["编号", "问题", "答案"])
    sheet.append([1, "Screen Size", ""])
    workbook.save(path)

    catalog = load_question_catalog(path)

    assert catalog.header_row == 3
    assert len(catalog.questions) == 1
    assert catalog.questions[0].question == "Screen Size"
    assert catalog.questions[0].number == "1"


def test_minimal_question_and_answer_header_is_still_supported(tmp_path):
    path = tmp_path / "minimal.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Question", "Answer"])
    sheet.append(["Warranty Summary", "1 year"])
    workbook.save(path)

    catalog = load_question_catalog(path)

    assert catalog.header_row == 1
    assert len(catalog.questions) == 1
    assert catalog.questions[0].answer == "1 year"
