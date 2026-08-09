# Windows Local Acceptance GUI

This GUI is a development shell around the existing ecommerce-agent acceptance and Makro execution chains. It does **not** replace or reinterpret product parsing, Resolver, Fill Plan, browser-write, persistence-verification, or safety logic.

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

The GUI is intentionally **not** packaged into a single-file EXE during active development.

## Presentation architecture

The active desktop shell uses **one native Qt Quick `ApplicationWindow` / `QQuickWindow` scene graph**. The previous QWidget / QOpenGLWidget presentation experiment has been removed from the runtime path and the legacy presentation files have been deleted.

On Windows the launcher selects:

- `QSG_RENDER_LOOP=threaded`
- Qt Quick `Direct3D11` graphics API

The continuously animated visual surface is therefore one retained scene graph rather than a QWidget backing-store plus OpenGL/FBO composition stack.

The scene graph contains:

- the sharp Fuji/sakura wallpaper;
- one pre-blurred companion image generated once at startup;
- fractional cursor parallax driven by `FrameAnimation` and real frame time;
- one full-window glass-mask layer shared by every card;
- one `MultiEffect` mask composition for all live glass regions;
- card-local translucent tint / hover / press feedback;
- exactly three sakura particles and the cursor follower in the same scene graph overlay.

Cards do **not** create their own wallpaper capture/FBO. Every card contributes only rounded geometry to the shared glass mask. Ancestor clipping is carried into the global mask so scroll-view/list-view cards do not leave blur outside their visible viewport.

The wallpaper, blur image, glass mask, sakura, cursor follower, controls, tables and logs all belong to the same QQuickWindow presentation system. Do not reintroduce a QQuickWidget, QOpenGLWidget, QWidget animation surface, per-card ShaderEffectSource, short-interval animation QTimer, or a second presentation shell.

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

## Gated real browser acceptance

Real execution remains locked until the current read-only four-stage acceptance has completed and produced a usable final Fill Plan.

When explicitly authorized, the GUI delegates to the canonical:

`makro_execute_listing.py`

It reuses the completed run's live schema, hot Resolver decision packet, supplier snapshot/screenshot and product evidence. The GUI does not implement a second browser-write path.

Permissions remain independent and explicit:

- Single section real fill defaults to no Save.
- Single section Save + reopen verification is opt-in.
- Full Step 3 is a persisted acceptance and therefore requires explicit Save authorization.
- Product Photos upload is opt-in and requires explicitly selected local files.
- `Send to QC` is permanently policy-locked and is never requested by the GUI.

## Makro browser safety

The read-only chain checks the configured Makro CDP endpoint before starting. If the long-lived Makro CDP endpoint is absent, the workflow stops; the GUI does not intentionally start/restart the Makro Edge profile.

Source capture remains on the independent source CDP port (default `9333`). If the supplier site requires legitimate manual verification/login, enable:

`Source Edge 已人工验证：采集当前页`

then retry after completing verification in the source browser.

## Safety indicators

The UI surfaces the current workflow safety state, including:

- Makro Write count
- Save state/count
- Send to QC state

For read-only runs the required state remains:

- `writes_performed = 0`
- `save_clicked = false`
- `send_to_qc_clicked = false`

For explicitly authorized real execution, writes and Save may occur according to the selected scope and permissions, while `Send to QC` must remain false.

## UI result sources

The GUI displays:

- READY count from the final Fill Plan;
- MISSING / CONFLICT from the hot final AI decision packet;
- BLOCKED count and reasons from the final Fill Plan;
- field name / AI result / final status / blocked reason / source / field ID;
- cold/hot Local and Web Resolver telemetry;
- source and Web cache behavior;
- Web candidate `same_product / different_product / uncertain` judgments;
- realtime read-only subprocess logs;
- realtime real-execution command/output/progress;
- real per-field execution and persistence results;
- final real execution report JSON;
- direct open of the current result directory.

Web candidate judgments are read from the current run's isolated semantic cache. This only surfaces the existing model output; it does not add a second product-matching layer.

## CI contracts

CI now validates three independent layers:

1. repository unit/contract tests;
2. a real offscreen QML load smoke test that constructs the Qt Quick shell;
3. the existing mock browser end-to-end probe.

Architecture tests also lock the active presentation path to the single Qt Quick scene graph and reject the former mixed-widget rendering design.
