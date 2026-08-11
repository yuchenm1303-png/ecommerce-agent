# Windows Local GUI

This GUI is a development shell around the existing production acceptance chain. It does **not** replace or reinterpret product parsing logic.

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

## Formal Makro browser session

The formal GUI owns one dedicated Microsoft Edge session for the Makro seller account.

Normal users do not need to launch a `9222` browser or manage a CDP port. `run_local_gui.py` installs `ManagedMakroBrowser`, which:

- uses the persistent `browser_profiles/makro-edge` profile;
- automatically launches the dedicated Edge when it is not running;
- automatically restores that browser after an idle-time close/crash;
- keeps authentication inside the Edge profile without reading/logging cookies or tokens;
- lets Single and Batch share the same authenticated browser session;
- lets Batch open multiple owned tabs in that same browser instead of one browser/login per product;
- keeps the existing `makro_target_id` ownership boundary for every Batch job;
- never closes the external Edge process itself.

If Makro authentication expires, the browser remains open on the normal Makro login flow. The GUI reports `LOGIN` and asks the user to complete the normal login, then retry. New tabs in the same browser/profile share that login state.

If the browser is restarted **after** a Single product or Batch has already been prepared, the previous Step 3 page / Chromium target IDs are no longer trusted. The GUI automatically restores the browser but refuses stale real execution and requires preparation to run again. This preserves exact tab/draft ownership rather than guessing a replacement page.

The underlying localhost CDP transport remains available to development CLI/tests as an implementation detail; the formal GUI hides it from normal controls.

Source capture remains on the independent source browser/CDP path (default `9333`). If the supplier site requires legitimate manual verification/login, enable:

`Source Edge 已人工验证：采集当前页`

then retry after completing the verification in the source browser.

## What preparation runs

The formal Single preparation follows the current staged workflow:

1. supplier Source Capture
2. Makro Step 1 / Vertical
3. Makro Step 2 / Brand
4. Step 3 live schema + current Resolver cold/hot + read-only Fill Plan

Preparation itself performs no Step 3 field writes, Save, or Send to QC. The separate real-execution gate is unlocked from the resulting Fill Plan.

Each GUI run gets its own directory under:

`logs/gui-runs/`

and its own run-local source/semantic cache directories.

## Safety indicators

The UI reads the existing manifests and displays:

- Makro Write count
- Save clicked
- Send to QC clicked

Preparation remains zero-write. Real execution is separately authorized and keeps the repository-wide rule:

- Save is explicit
- Product Photos are explicit
- `Send to QC` is locked and must remain false

## UI result sources

The GUI displays:

- READY count from the final Fill Plan
- MISSING / CONFLICT from the final AI decision packet
- BLOCKED count and reasons from the final Fill Plan
- field name / AI result / final status / blocked reason / source
- cold/hot model calls and cache behavior
- Web candidate `same_product / different_product / uncertain` judgments
- realtime subprocess log
- real browser execution result/report
- direct open of current result/log directories

Web candidate judgments are read from this GUI run's semantic cache. This surfaces the existing `app/web_enrichment.py` model output; it does not add a second product-matching layer.

## Visual style

The current formal GUI keeps the native Quick Fuji/Sakura background with QWidget business controls and lightweight local progress/interaction effects. Browser-session management is independent from those rendering paths.
