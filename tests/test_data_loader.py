from __future__ import annotations

import csv

from openpyxl import Workbook

from app.data_loader import load_products


def test_load_csv(tmp_path):
    path = tmp_path / "products.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["SKU", "品牌", "额定功率"])
        writer.writerow(["A001", "LumaTech", "60W"])

    products = load_products(path)
    assert len(products) == 1
    assert products[0].sku == "A001"
    assert products[0].values["品牌"] == "LumaTech"
    assert products[0].values["额定功率"] == "60W"


def test_load_xlsx(tmp_path):
    path = tmp_path / "products.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["商品编码", "品牌", "色温"])
    sheet.append(["A002", "BrightHome", "4000K"])
    workbook.save(path)

    products = load_products(path)
    assert products[0].sku == "A002"
    assert products[0].values["色温"] == "4000K"
