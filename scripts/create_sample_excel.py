from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "products.csv"
XLSX_PATH = ROOT / "data" / "products.xlsx"


def main() -> None:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    for row in rows:
        sheet.append(row)

    workbook.save(XLSX_PATH)
    print(f"已生成：{XLSX_PATH}")


if __name__ == "__main__":
    main()
