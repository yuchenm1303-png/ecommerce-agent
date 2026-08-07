from __future__ import annotations

from pathlib import Path

import pytest

from makro_probe import (
    build_launch_kwargs,
    build_parser,
    build_semantic_fields,
    capture_controls,
    derive_attribute_key,
    merge_scans,
    sanitize_dom_snapshot,
    scroll_and_capture,
)


# ---------------------------------------------------------------------------
# Pure helper tests (no browser required).
# ---------------------------------------------------------------------------

def test_parser_url_is_optional() -> None:
    args = build_parser().parse_args([])
    assert args.url is None
    assert args.browser == "edge"
    assert args.profile_dir == "browser_profiles/makro-edge"


def test_parser_defaults_to_edge_and_isolated_profile() -> None:
    args = build_parser().parse_args(
        ["--url", "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x"]
    )
    assert args.browser == "edge"
    assert args.profile_dir == "browser_profiles/makro-edge"


def test_parser_flags() -> None:
    args = build_parser().parse_args(
        [
            "--url",
            "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x",
            "--browser",
            "chromium",
            "--include-values",
            "--open-dropdowns",
            "--no-dom-snapshot",
            "--scan-sections",
            "--headless",
        ]
    )
    assert args.browser == "chromium"
    assert args.include_values is True
    assert args.open_dropdowns is True
    assert args.no_dom_snapshot is True
    assert args.scan_sections is True
    assert args.headless is True


def test_build_launch_kwargs_edge_uses_msedge_channel() -> None:
    from pathlib import Path

    kwargs = build_launch_kwargs(
        browser="edge",
        profile_dir=Path("browser_profiles/makro-edge"),
        headless=False,
    )
    assert kwargs["channel"] == "msedge"
    assert kwargs["user_data_dir"] == str(Path("browser_profiles/makro-edge"))
    assert kwargs["headless"] is False


def test_build_launch_kwargs_chromium_has_no_channel() -> None:
    from pathlib import Path

    kwargs = build_launch_kwargs(
        browser="chromium",
        profile_dir=Path("browser_profiles/makro-edge"),
        headless=True,
    )
    assert "channel" not in kwargs
    assert kwargs["headless"] is True


def test_merge_scans_dedupes_by_path_and_keeps_richer_entry() -> None:
    scans = [
        [{"path": "body > div#a > input:nth-of-type(1)", "label": "", "ordinal": 0}],
        [
            {
                "path": "body > div#a > input:nth-of-type(1)",
                "label": "Base Price",
                "options": [{"text": "x"}],
                "ordinal": 0,
            },
            {"path": "body > div#a > input:nth-of-type(2)", "label": "MOQ", "ordinal": 1},
        ],
    ]
    merged = merge_scans(scans)
    assert len(merged) == 2
    by_path = {item["path"]: item for item in merged}
    assert by_path["body > div#a > input:nth-of-type(1)"]["label"] == "Base Price"
    assert [item["ordinal"] for item in merged] == [0, 1]


def test_sanitize_dom_snapshot_removes_values_scripts_and_secrets() -> None:
    html = """<!doctype html><html><body>
      <script>window.token = "SECRET";</script>
      <form>
        <label for="p">价格</label>
        <input id="p" value="999.00" data-token="abc123">
        <select id="s" value="1"><option value="1">One</option></select>
        <textarea id="t">secret text</textarea>
        <input type="password" value="hunter2">
        <input name="session_key" value="xyz">
      </form>
      <p>eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c</p>
    </body></html>"""
    result = sanitize_dom_snapshot(html)
    assert "<script" not in result
    assert "SECRET" not in result
    assert 'value="999.00"' not in result
    assert "data-token" not in result
    assert "secret text" not in result
    assert 'value="hunter2"' not in result
    assert "session_key" not in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert 'id="p"' in result
    assert 'id="s"' in result
    assert "One" in result
    assert "价格" in result


def test_sanitize_dom_snapshot_keeps_structure() -> None:
    html = '<div class="card"><label for="x">SKU ID *</label><input id="x" value="A001"></div>'
    result = sanitize_dom_snapshot(html)
    assert '<div class="card">' in result
    assert '<label for="x">' in result
    assert '<input id="x" />' in result or '<input id="x">' in result

# ---------------------------------------------------------------------------
# Browser-based probe tests (skipped when Playwright Chromium is unavailable).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def headless_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"playwright not installed: {exc}")
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Chromium unavailable: {exc}")
    page = browser.new_page()
    yield page
    browser.close()
    playwright.stop()


@pytest.mark.probe
def test_probe_captures_native_controls(headless_page) -> None:
    headless_page.set_content(
        """
        <form>
          <div>
            <label for="sku">SKU ID *</label>
            <input id="sku" name="sku" type="text" required placeholder="e.g. 1001">
          </div>
          <div>
            <label for="desc">Description</label>
            <textarea id="desc" name="description"></textarea>
          </div>
          <div>
            <label for="status">Listing Status</label>
            <select id="status" name="status">
              <option value="">Select</option>
              <option value="draft">Draft</option>
              <option value="active">Active</option>
            </select>
          </div>
          <label><input type="checkbox" name="hazardous"> Hazardous</label>
          <label><input type="radio" name="condition" value="new"> New</label>
          <label><input type="radio" name="condition" value="refurb"> Refurbished</label>
        </form>
        """
    )
    controls = capture_controls(headless_page, include_values=False)
    by_id = {item["id"]: item for item in controls if item.get("id")}
    assert by_id["sku"]["label"] == "SKU ID"
    assert by_id["sku"]["required"] is True
    assert by_id["sku"]["field_kind"] == "input"
    assert by_id["desc"]["field_kind"] == "textarea"
    assert by_id["status"]["field_kind"] == "select"
    option_texts = {opt["text"] for opt in by_id["status"]["options"]}
    assert {"Draft", "Active"} <= option_texts
    by_name = {item["name"]: item for item in controls if item.get("name")}
    assert by_name["hazardous"]["field_kind"] == "checkbox"
    assert by_name["condition"]["field_kind"] == "radio"


@pytest.mark.probe
def test_probe_never_records_sensitive_values_by_default(headless_page) -> None:
    headless_page.set_content(
        """
        <input id="price" name="price" value="1999.00">
        <input id="pw" type="password" name="password" value="hunter2">
        <input id="token" name="token" value="abc123">
        """
    )
    controls = capture_controls(headless_page, include_values=False)
    assert all("value" not in item for item in controls)
    ids = {item["id"] for item in controls}
    assert "pw" not in ids
    assert "price" in ids

    with_values = capture_controls(headless_page, include_values=True)
    price = next(item for item in with_values if item.get("id") == "price")
    assert price["value"] == "1999.00"
    assert price["value_recorded"] is True
    # Sensitive-named fields keep metadata but never expose their value.
    token = next(item for item in with_values if item.get("id") == "token")
    assert token["value_recorded"] is False
    assert "value" not in token
    assert all(item.get("id") != "pw" for item in with_values)


@pytest.mark.probe
def test_probe_detects_custom_dropdown(headless_page) -> None:
    headless_page.set_content(
        """
        <div id="widget">
          <span id="widget-label">Material</span>
          <div id="material" role="combobox" aria-labelledby="widget-label" aria-expanded="false" tabindex="0">
            <span>Select material</span>
          </div>
          <ul id="material-options" role="listbox" style="display:none">
            <li role="option" data-value="steel">Steel</li>
            <li role="option" data-value="aluminum">Aluminum</li>
          </ul>
        </div>
        """
    )
    controls = capture_controls(headless_page)
    combobox = [item for item in controls if item.get("role") == "combobox"]
    assert combobox, controls
    item = combobox[0]
    assert item["field_kind"] == "dropdown"
    assert item["label"] == "Material"
    assert item["has_dropdown_options"] is True
    texts = {opt["text"] for opt in item["options"]}
    assert {"Steel", "Aluminum"} <= texts


@pytest.mark.probe
def test_scroll_and_capture_discovers_lazy_fields(headless_page) -> None:
    headless_page.set_content(
        """
        <div id="scroller" style="height:200px; overflow-y:auto; border:1px solid #000;">
          <div id="top">
            <label for="f1">Field One</label>
            <input id="f1" name="f1">
          </div>
          <div id="lazy" style="height:1200px;"></div>
        </div>
        """
    )
    headless_page.evaluate(
        """
        () => {
          const scroller = document.getElementById('scroller');
          const lazy = document.getElementById('lazy');
          scroller.addEventListener('scroll', () => {
            if (scroller.scrollTop > 500 && !document.getElementById('f2')) {
              const box = document.createElement('div');
              box.innerHTML = '<label for="f2">Field Two</label><input id="f2" name="f2">';
              lazy.prepend(box);
            }
          });
        }
        """
    )
    controls, stats = scroll_and_capture(
        headless_page,
        include_values=False,
        wait_ms=30,
        max_scroll_steps=50,
    )
    paths = " ".join(item["path"] for item in controls)
    assert "f2" in paths, paths
    assert stats["scroll_containers_found"] >= 1
    assert stats["scroll_passes"] >= 1



@pytest.mark.probe
def test_wait_probes_current_page_without_re_navigation(headless_page, monkeypatch) -> None:
    """After Enter the probe scans the CURRENT page and never re-gotos."""
    from makro_probe import MAKRO_HOME_URL, wait_for_authenticated_listing

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1>Add a Single Listing</h1><div>ADD PRODUCT INFO</div></body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")
    wait_for_authenticated_listing(headless_page, None, timeout_s=5)
    assert headless_page.url.startswith(MAKRO_HOME_URL)


@pytest.mark.probe
def test_wait_raises_when_current_page_is_not_listing(headless_page, monkeypatch) -> None:
    from makro_probe import wait_for_authenticated_listing

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body>Dashboard</body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")
    with pytest.raises(RuntimeError, match="不会自动跳转"):
        wait_for_authenticated_listing(headless_page, None, timeout_s=2)


@pytest.mark.probe
def test_wait_returns_early_when_valid_url_lands_on_listing(headless_page, monkeypatch) -> None:
    """A validated --url that renders markers probes immediately without prompt."""
    from makro_probe import wait_for_authenticated_listing

    url = "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x"

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1>Add a Single Listing</h1></body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)

    def forbidden_input(*args, **kwargs):
        raise AssertionError("input() must not be called when already on the listing page")

    monkeypatch.setattr("builtins.input", forbidden_input)
    wait_for_authenticated_listing(headless_page, url, timeout_s=5)
    assert "Add a Single Listing" in headless_page.content()



@pytest.mark.probe
def test_makro_fixture_labels_and_required(headless_page) -> None:
    """Real Makro DOM structure: labels from sibling wrappers, stars for required."""
    html = Path(__file__).parent.joinpath("fixtures", "makro_listing_fixture.html").read_text(encoding="utf-8")
    headless_page.set_content(html)
    controls = capture_controls(headless_page)
    by_id = {item["id"]: item for item in controls if item.get("id")}

    assert by_id["sku_id"]["label"] == "SKU ID"
    assert by_id["sku_id"]["required"] is True
    assert by_id["sku_id"]["required_hint"] == "mandatory-star"

    assert by_id["listing_status"]["label"] == "Listing Status"
    assert by_id["listing_status"]["required"] is True
    assert by_id["listing_status"]["field_kind"] == "select"
    option_texts = {opt["text"] for opt in by_id["listing_status"]["options"]}
    assert {"Active", "Inactive"} <= option_texts

    assert by_id["mrp"]["label"] == "Base Price"
    assert by_id["flipkart_selling_price"]["label"].lower() == "your selling price"
    assert by_id["country_of_origin"]["label"] == "Country Of Origin"
    assert by_id["country_of_origin"]["required"] is True

    # section title comes from the listing card, subsection from WebsiteSectionName
    assert by_id["sku_id"]["section_heading"] and "Price, Stock" in by_id["sku_id"]["section_heading"]
    assert by_id["sku_id"]["subsection_heading"] == "Listing information"


@pytest.mark.probe
def test_scan_sections_expands_and_scans_collapsed_section(headless_page) -> None:
    """--scan-sections: expand Product Description via EDIT, scan, then Cancel."""
    from makro_probe import scan_sections

    html = Path(__file__).parent.joinpath("fixtures", "makro_listing_fixture.html").read_text(encoding="utf-8")
    headless_page.set_content(html)
    sections, controls, stats = scan_sections(headless_page, wait_ms=50, max_scroll_steps=20)

    titles = [section["title"] for section in sections]
    assert any("Price, Stock" in title for title in titles)
    assert any("Product Description" in title for title in titles)

    by_id = {item["id"]: item for item in controls if item.get("id")}
    assert "sku_id" in by_id
    assert by_id["product_description"]["label"] == "Product Description"
    assert by_id["product_description"]["required"] is True
    assert by_id["product_description"]["section_heading"] and "Product Description" in by_id["product_description"]["section_heading"]

    desc_section = next(s for s in sections if "Product Description" in s["title"])
    assert desc_section["field_count"] >= 1
    assert any(c.get("id") == "product_description" for c in desc_section["controls"])

    assert stats["sections_found"] == 2
    assert stats["sections_expanded_by_scan"] >= 1
    assert stats["sections_cancelled"] >= 1
    # Cancel collapsed the section again
    assert headless_page.locator("#product-description-body").inner_html().strip() == ""



# ---------------------------------------------------------------------------
# Keep-open session model: login detection, repeat scans, capture_listing.
# ---------------------------------------------------------------------------

def test_ask_yes_no(monkeypatch) -> None:
    from makro_probe import _ask_yes_no

    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "Y")
    assert _ask_yes_no("继续扫描下一个页面？ ") is True
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "yes")
    assert _ask_yes_no("继续扫描下一个页面？ ") is True
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")
    assert _ask_yes_no("继续扫描下一个页面？ ") is True
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "n")
    assert _ask_yes_no("继续扫描下一个页面？ ") is False
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "N")
    assert _ask_yes_no("继续扫描下一个页面？ ") is False


def test_profile_artifacts(tmp_path) -> None:
    from makro_probe import _profile_artifacts

    assert "等待浏览器写入" in _profile_artifacts(tmp_path)
    (tmp_path / "Local State").write_text("x", encoding="utf-8")
    assert "Local State" in _profile_artifacts(tmp_path)


@pytest.mark.probe
def test_is_logged_in_false_on_login_form(headless_page) -> None:
    from makro_probe import _is_logged_in

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body='<html><body><form><input type="password" name="password"></form>'
            "<button>Sign in</button></body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)
    headless_page.goto("https://seller.makro.co.za/")
    assert _is_logged_in(headless_page) is False


@pytest.mark.probe
def test_is_logged_in_true_on_authenticated_shell(headless_page) -> None:
    from makro_probe import _is_logged_in

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><header>My Account · <a>Sign Out</a></header>"
            "<div>Dashboard</div></body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)
    headless_page.goto("https://seller.makro.co.za/")
    assert _is_logged_in(headless_page) is True


@pytest.mark.probe
def test_wait_repeat_iteration_keeps_current_page(headless_page, monkeypatch) -> None:
    """navigate_first=False must not goto the home page (same Edge session)."""
    from makro_probe import wait_for_authenticated_listing

    def handle(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1>Add a Single Listing</h1><div>ADD PRODUCT INFO</div></body></html>",
        )

    headless_page.route("https://seller.makro.co.za/**", handle)
    headless_page.goto(
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x"
    )

    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "")
    wait_for_authenticated_listing(headless_page, None, timeout_s=5, navigate_first=False)
    assert "addListings/single" in headless_page.url


@pytest.mark.probe
def test_wait_detects_existing_login_and_skips_manual_login(headless_page, monkeypatch) -> None:
    """Logged-in home page: no manual login step, only the final Enter."""
    from makro_probe import wait_for_authenticated_listing

    body = (
        "<html><body><header>My Account · <a>Sign Out</a></header>"
        "<h1>Add a Single Listing</h1><div>ADD PRODUCT INFO</div></body></html>"
    )

    def handle(route):
        route.fulfill(status=200, content_type="text/html", body=body)

    headless_page.route("https://seller.makro.co.za/**", handle)

    prompts = []

    def fake_input(*args, **kwargs):
        prompts.append(args)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    wait_for_authenticated_listing(headless_page, None, timeout_s=5)
    assert headless_page.url.startswith("https://seller.makro.co.za/")
    assert len(prompts) == 1  # only the "press Enter" step, no login wait


@pytest.mark.probe
def test_capture_listing_writes_outputs(headless_page, tmp_path) -> None:
    from makro_probe import capture_listing

    headless_page.set_content(
        '<html><body><form><label for="sku">SKU ID</label>'
        '<input id="sku" name="sku"></form></body></html>'
    )
    payload = capture_listing(
        headless_page,
        output_dir=tmp_path,
        stamp="20260807-test",
        include_values=False,
        open_dropdowns=False,
        scan_sections_mode=False,
        no_dom_snapshot=False,
        scroll_wait_ms=30,
        max_scroll_steps=5,
    )
    assert (tmp_path / "makro-fields-20260807-test.json").exists()
    assert (tmp_path / "makro-page-20260807-test.png").exists()
    assert (tmp_path / "makro-dom-20260807-test.html").exists()
    assert payload["control_count"] >= 1
    assert payload["sections"] is None
    assert payload["include_values"] is False
    assert payload["dom_snapshot_saved"] is True


@pytest.mark.probe
def test_capture_listing_sections_mode_uses_fixture(headless_page, tmp_path) -> None:
    from makro_probe import capture_listing

    html = Path(__file__).parent.joinpath("fixtures", "makro_listing_fixture.html").read_text(encoding="utf-8")
    headless_page.set_content(html)
    payload = capture_listing(
        headless_page,
        output_dir=tmp_path,
        stamp="20260807-sections",
        include_values=False,
        open_dropdowns=False,
        scan_sections_mode=True,
        no_dom_snapshot=True,
        scroll_wait_ms=30,
        max_scroll_steps=20,
    )
    assert payload["sections"] and isinstance(payload["sections"], list)
    titles = [s["title"] for s in payload["sections"]]
    assert any("Price, Stock" in t for t in titles)
    assert any("Product Description" in t for t in titles)
    assert payload["dom_snapshot_saved"] is False


# ---------------------------------------------------------------------------
# Semantic field grouping (real-Makro-structure fixtures, no real data).
# ---------------------------------------------------------------------------

def test_derive_attribute_key_prefers_id_then_indexed_name() -> None:
    assert derive_attribute_key({"id": "keywords", "name": "keywords_0_value"}) == "keywords"
    assert derive_attribute_key({"id": "keywords", "name": "keywords_4_value"}) == "keywords"
    assert derive_attribute_key({"id": "", "name": "flash_memory_0_qualifier"}) == "flash_memory"
    assert derive_attribute_key({"id": "sku_id", "name": "sku_id_0_value"}) == "sku_id"
    assert derive_attribute_key({"id": "", "name": "", "label": "Search for SKU ID"}) == "search_for_sku_id"
    assert derive_attribute_key({"id": "mrp", "name": "mrp_0_value", "label": ""}) == "mrp"


def test_build_semantic_fields_merges_multi_value_and_qualifiers() -> None:
    controls = [
        {"id": "sales_package", "name": "sales_package_0_value", "label": "Sales Package",
         "required": True, "required_hint": "mandatory-star", "field_kind": "input",
         "section_heading": "Product Description", "subsection_heading": "GENERAL"},
        {"id": "sales_package", "name": "sales_package_1_value", "label": "Sales Package",
         "required": True, "required_hint": "mandatory-star", "field_kind": "input",
         "section_heading": "Product Description", "subsection_heading": "GENERAL"},
        {"id": "", "name": "flash_memory_0_qualifier", "label": "Flash Memory",
         "field_kind": "select", "section_heading": "Product Description", "subsection_heading": "GENERAL",
         "options": [{"text": "MB", "value": "MB"}, {"text": "GB", "value": "GB"}]},
        {"id": "flash_memory", "name": "flash_memory_0_value", "label": "Flash Memory",
         "field_kind": "input", "section_heading": "Product Description", "subsection_heading": "GENERAL"},
        {"id": "sku_id", "name": "sku_id_0_value", "label": "SKU ID",
         "required": True, "required_hint": "mandatory-star", "field_kind": "input",
         "section_heading": "Price, Stock and Shipping Information", "subsection_heading": "Listing information"},
    ]
    fields = build_semantic_fields(controls)
    by_key = {f["attribute_key"]: f for f in fields}

    sales = by_key["sales_package"]
    assert sales["multi_value"] is True
    assert len(sales["controls"]) == 2
    assert sales["required"] is True
    assert sales["required_hint"] == "mandatory-star"

    flash = by_key["flash_memory"]
    assert flash["multi_value"] is False
    assert len(flash["controls"]) == 2
    assert flash["field_kind"] == "input"
    assert flash["accepted_control_kinds"] == ["input", "select"]
    assert {o["text"] for o in flash["options"]} == {"MB", "GB"}

    # Same attribute key in a different section stays a separate semantic field.
    assert by_key["sku_id"]["section_heading"] == "Price, Stock and Shipping Information"


def _scan_fixture(headless_page, name: str, *, wait_ms: int = 30, max_scroll_steps: int = 30):
    from makro_probe import scan_sections

    html = Path(__file__).parent.joinpath("fixtures", name).read_text(encoding="utf-8")
    headless_page.set_content(html, wait_until="load")
    return scan_sections(headless_page, wait_ms=wait_ms, max_scroll_steps=max_scroll_steps)


def _section(sections, needle):
    return next(s for s in sections if needle in s["title"])


@pytest.mark.probe
def test_semantic_fields_sports_fixture_pd_36_not_50(headless_page) -> None:
    sections, controls, stats = _scan_fixture(headless_page, "makro_listing_fixture_sports.html")
    pd = _section(sections, "Product Description")
    ad = _section(sections, "Additional Description")
    price = _section(sections, "Price, Stock")

    assert pd["field_count"] == 50
    assert pd["semantic_field_count"] == 36
    assert ad["semantic_field_count"] == 28
    assert price["semantic_field_count"] >= 14

    pd_keys = {f["attribute_key"] for f in pd["semantic_fields"]}
    ad_keys = {f["attribute_key"] for f in ad["semantic_fields"]}
    assert {"sales_package", "ports", "shooting_modes"} <= pd_keys
    assert {"keywords", "other_features"} <= ad_keys
    # multi-value attributes produce exactly one semantic field
    sales = next(f for f in pd["semantic_fields"] if f["attribute_key"] == "sales_package")
    assert sales["multi_value"] is True
    assert len(sales["controls"]) == 5
    keywords = next(f for f in ad["semantic_fields"] if f["attribute_key"] == "keywords")
    assert keywords["multi_value"] is True
    assert len(keywords["controls"]) == 5

    # sections are collapsed again via the safe Cancel
    assert stats["sections_found"] == 4
    assert stats["sections_expanded_by_scan"] == 4
    assert stats["sections_cancelled"] == 4
    assert _section(sections, "Product Photos")["image_count"] == 5


@pytest.mark.probe
def test_semantic_fields_vehicle_blank_and_filled_stay_consistent(headless_page) -> None:
    blank_sections, blank_controls, _ = _scan_fixture(headless_page, "makro_listing_fixture_vehicle_blank.html")
    filled_sections, filled_controls, _ = _scan_fixture(headless_page, "makro_listing_fixture_vehicle_filled.html")

    blank_pd = _section(blank_sections, "Product Description")
    filled_pd = _section(filled_sections, "Product Description")
    blank_ad = _section(blank_sections, "Additional Description")
    filled_ad = _section(filled_sections, "Additional Description")

    assert blank_pd["semantic_field_count"] == 14
    assert filled_pd["semantic_field_count"] == 14
    assert blank_ad["semantic_field_count"] == 46
    assert filled_ad["semantic_field_count"] == 46

    # More DOM controls in the filled listing, but the semantic set stays the same.
    assert blank_pd["field_count"] == 14
    assert filled_pd["field_count"] == 21
    assert len(blank_pd["semantic_fields"]) == len(filled_pd["semantic_fields"])
    assert {f["attribute_key"] for f in blank_pd["semantic_fields"]} == {
        f["attribute_key"] for f in filled_pd["semantic_fields"]
    }
    assert {f["attribute_key"] for f in blank_ad["semantic_fields"]} == {
        f["attribute_key"] for f in filled_ad["semantic_fields"]
    }

    # Multi-value fields aggregate into one semantic field with all controls.
    filled_sales = next(f for f in filled_pd["semantic_fields"] if f["attribute_key"] == "sales_package")
    assert filled_sales["multi_value"] is True
    assert len(filled_sales["controls"]) == 7
    blank_sales = next(f for f in blank_pd["semantic_fields"] if f["attribute_key"] == "sales_package")
    assert blank_sales["multi_value"] is False
    assert len(blank_sales["controls"]) == 1

    filled_res = next(f for f in filled_pd["semantic_fields"] if f["attribute_key"] == "recording_resolution")
    assert filled_res["multi_value"] is True
    assert len(filled_res["controls"]) == 2

    # Qualifier pair (value input + unit select) is one non-multi semantic field.
    battery = next(f for f in filled_ad["semantic_fields"] if f["attribute_key"] == "battery_life")
    assert battery["multi_value"] is False
    assert len(battery["controls"]) == 2
    assert battery["field_kind"] == "input"
    assert battery["accepted_control_kinds"] == ["input", "select"]


@pytest.mark.probe
def test_price_section_unified_expand_captures_real_fields(headless_page) -> None:
    sections, controls, stats = _scan_fixture(headless_page, "makro_listing_fixture_vehicle_blank.html")
    price = _section(sections, "Price, Stock")
    assert price["expanded"] is True
    keys = {f["attribute_key"] for f in price["semantic_fields"]}
    assert {
        "sku_id",
        "listing_status",
        "mrp",
        "flipkart_selling_price",
        "minimum_order_quantity",
        "country_of_origin",
    } <= keys
    sku = next(f for f in price["semantic_fields"] if f["attribute_key"] == "sku_id")
    assert sku["required"] is True
    assert sku["required_hint"] == "mandatory-star"
    assert sku["subsection_heading"] == "Listing information"


@pytest.mark.probe
def test_capture_listing_payload_has_semantic_fields(headless_page, tmp_path) -> None:
    from makro_probe import capture_listing

    html = Path(__file__).parent.joinpath("fixtures", "makro_listing_fixture_vehicle_blank.html").read_text(encoding="utf-8")
    headless_page.set_content(html, wait_until="load")
    payload = capture_listing(
        headless_page,
        output_dir=tmp_path,
        stamp="20260807-semantic",
        include_values=False,
        open_dropdowns=False,
        scan_sections_mode=True,
        no_dom_snapshot=True,
        scroll_wait_ms=30,
        max_scroll_steps=30,
    )
    assert payload["semantic_field_count"] == len(payload["semantic_fields"])
    # 18 price + 14 product description + 46 additional description
    assert payload["semantic_field_count"] == 78
    assert payload["control_count"] >= payload["semantic_field_count"]
