from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .fill_plan import LiveFillPlan


PLAN_COLUMNS = (
    "Section",
    "Attribute Key",
    "Makro Label",
    "Required",
    "Action",
    "Question No.",
    "QA Question",
    "Match Basis",
    "Answer",
    "Resolution Status",
    "Auto Fill Eligible",
    "Confidence",
    "Source Type",
    "Source Reference",
    "Evidence",
    "Reason",
    "Provenance JSON",
)


def write_fill_plan_json(plan: LiveFillPlan, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(plan.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def write_fill_plan_xlsx(plan: LiveFillPlan, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Fill Plan"
    sheet.append(list(PLAN_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    for item in plan.items:
        resolution = item.resolution
        sheet.append(
            [
                item.section_heading,
                item.attribute_key,
                item.label,
                "YES" if item.required else "NO",
                item.action,
                item.question_number,
                item.question,
                item.match_basis,
                resolution.answer or "",
                resolution.status,
                "YES" if resolution.eligible_for_autofill else "NO",
                round(resolution.confidence, 4),
                resolution.source_type or "",
                resolution.source_reference or "",
                resolution.evidence or "",
                item.reason,
                json.dumps(resolution.provenance, ensure_ascii=False),
            ]
        )

    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(PLAN_COLUMNS))}{max(1, len(plan.items) + 1)}"
    )
    widths = {
        1: 34,
        2: 30,
        3: 32,
        4: 10,
        5: 12,
        6: 12,
        7: 32,
        8: 18,
        9: 28,
        10: 18,
        11: 16,
        12: 12,
        13: 20,
        14: 54,
        15: 38,
        16: 58,
        17: 72,
    }
    for index, width in widths.items():
        sheet.column_dimensions[get_column_letter(index)].width = width

    summary = workbook.create_sheet("Summary")
    summary.append(["Metric", "Value"])
    summary["A1"].font = Font(bold=True)
    summary["B1"].font = Font(bold=True)
    for key, value in plan.summary().items():
        summary.append([key, value])
    summary.column_dimensions["A"].width = 38
    summary.column_dimensions["B"].width = 18

    unmatched = workbook.create_sheet("Unmatched QA")
    unmatched.append(["No.", "Question", "Status", "Detail"])
    for cell in unmatched[1]:
        cell.font = Font(bold=True)
    for item in plan.unmatched_questions:
        unmatched.append(
            [item["number"], item["question"], item["status"], item["detail"]]
        )
    unmatched.column_dimensions["A"].width = 10
    unmatched.column_dimensions["B"].width = 36
    unmatched.column_dimensions["C"].width = 16
    unmatched.column_dimensions["D"].width = 70

    workbook.save(target)
    return target
