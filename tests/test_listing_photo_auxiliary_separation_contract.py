from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "listing_photo_ownership.py").read_text(encoding="utf-8")


def test_single_and_batch_expose_separate_auxiliary_and_product_photo_surfaces() -> None:
    assert 'button.setText(f"辅助资料 {len(files)}" if files else "辅助资料…")' in SOURCE
    assert 'QPushButton("商品图片 0")' in SOURCE
    assert 'button.setText(f"商品图片 {len(images)}" if images else "商品图片…")' in SOURCE
    assert 'real_picker.setText("管理商品图片…")' in SOURCE


def test_auxiliary_images_are_never_promoted_to_manual_listing_photos() -> None:
    assert 'row.listing_photo_files' in SOURCE
    assert 'files = getattr(row, "product_files", ())' not in SOURCE
    assert '"source": "user_selected_listing_photos"' in SOURCE
    assert '"auxiliary_images_uploadable": False' in SOURCE


def test_automatic_photo_fallback_excludes_customer_pack_images() -> None:
    assert 'customer_images = _customer_pack_images(outputs)' in SOURCE
    assert 'if path in customer_images' in SOURCE
    assert 'chosen = manual or _supplier_listing_images(Path(job.run_dir))' in SOURCE
    assert '"auxiliary_images=excluded"' in SOURCE


def test_auxiliary_copy_explains_ai_only_semantics() -> None:
    assert 'subtitle.setText("只给 AI / Resolver 看 · PDF / Word / 表格 / 图片 / ZIP")' in SOURCE
    assert '辅助资料中的图片只给 AI / Resolver 看，不会写入 Makro Product Photos' in SOURCE
