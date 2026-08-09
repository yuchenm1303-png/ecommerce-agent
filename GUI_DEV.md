# Windows Local Read-only GUI

This GUI is a development shell around the existing production read-only acceptance chain. It does **not** replace or reinterpret product parsing logic.

## Branch / worktree safety

GUI development lives on:

`feat/local-test-gui`

The branch was created from:

`feat/review-preview-gate`

Do not switch the original dirty worktree. Create a second worktree instead:

```powershell
# Run from the existing ecommerce-agent repository.
powershell -ExecutionPolicy Bypass -File scripts/create_gui_worktree.ps1
```

Equivalent manual commands:

```powershell
git fetch origin feat/local-test-gui
git worktree add --track -b feat/local-test-gui ..\ecommerce-agent-gui origin/feat/local-test-gui
```

If the local branch already exists:

```powershell
git worktree add ..\ecommerce-agent-gui feat/local-test-gui
```

No reset/stash/clean/checkout of the original worktree is required.

## Install

Use the same Python environment that runs ecommerce-agent core code:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-gui.txt
```

PySide6 is intentionally isolated in `requirements-gui.txt`; the core runtime dependency file is unchanged.

## Start during development

```powershell
python run_local_gui.py
```

The GUI is intentionally **not** packaged into a single-file EXE during active development. Editing Python files and restarting the launcher is enough.

## What “只读测试” runs

One click executes the existing canonical acceptance order:

1. `makro_plan_listing.py --scan-live-schema`
2. cold `makro_resolve_ai.py`
3. hot `makro_resolve_ai.py`
4. `makro_plan_listing.py --decision-packet ...` read-only Fill Plan

Each GUI run gets its own directory:

`logs/gui-runs/readonly-YYYYMMDD-HHMMSS/`

and its own temporary caches:

- `_cache/source`
- `_cache/semantic`

The cold resolver uses `--refresh-source`. The hot resolver reuses the exact run-local source and semantic caches, so cache behavior is visible without depending on older unrelated tests.

## Makro browser safety

The GUI checks `http://127.0.0.1:9222/json/version` before starting.

If the Makro CDP endpoint is absent, the GUI stops. It does not intentionally start/restart the long-lived Makro Edge profile.

Source capture remains on the existing independent source CDP port (default `9333`). If the supplier site requires legitimate manual verification/login, enable:

`Source Edge 已人工验证：采集当前页`

then retry after completing the verification in the source browser.

## Safety indicators

The UI reads the existing manifests and displays:

- Makro Write count
- Save clicked
- Send to QC clicked

The GUI runner never invokes `makro_execute_listing.py`.

Expected result for every GUI read-only run:

- `writes_performed = 0`
- `save_clicked = false`
- `send_to_qc_clicked = false`

## UI result sources

The GUI displays:

- READY count from the final Fill Plan
- MISSING / CONFLICT from the hot final AI decision packet
- BLOCKED count and reasons from the final Fill Plan
- field name / AI result / final status / blocked reason / source
- cold/hot Local batch counts, model calls, cache hits and failures
- cold/hot source cache and Web cache hits
- Web candidate `same_product / different_product / uncertain` judgments
- realtime subprocess log
- direct open of the current result/log directory

Web candidate judgments are read from this GUI run's isolated `web-product-research-*.json` semantic cache. This only surfaces the existing `app/web_enrichment.py` model output; it does not add a second product-matching layer.

## Visual style

The first version deliberately favors test usability over animation. It uses a modern glass-card shell inspired by the earlier `nekro.top`-style personal homepage: large atmospheric background, translucent rounded panels, soft pink/lilac accents, and clear status cards. It does not use the later Win98/pixel homepage style.
