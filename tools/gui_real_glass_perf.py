from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui.native_background as bg


VARIANTS = ("full-window", "regional")
_ORIGINAL_QML = bg._qml_source


def _regional_qml_source() -> str:
    """Replace the full-window mask/effect pass with stable card-sized glass layers.

    The wallpaper asset, pre-blur, parallax coordinates, card geometry, radius,
    alpha and QWidget foreground remain identical. Each visible card gets an
    explicit source item, mask item and MultiEffect. The effect is no longer a
    layer.effect owned by the same item it renders, so source/mask lifetime is
    independent from delegate clipping and visibility changes.
    """

    source = _ORIGINAL_QML()
    start_marker = "\n    Item {\n        id: blurSource"
    overlay_marker = (
        "\n    Repeater {\n"
        "        model: glassCardModel\n"
        "        delegate: Item {\n"
        "            x: 0\n"
    )
    start = source.index(start_marker)
    end = source.index(overlay_marker, start)
    regional = f'''
    Repeater {{
        model: glassCardModel
        delegate: Item {{
            x: clipX
            y: clipY
            width: Math.max(0, clipW)
            height: Math.max(0, clipH)
            clip: true
            visible: cardVisible && width > 0 && height > 0

            Item {{
                id: regionalGlass
                x: cardX - clipX
                y: cardY - clipY
                width: cardW
                height: cardH
                visible: width > 0 && height > 0

                Item {{
                    id: regionalSource
                    anchors.fill: parent
                    clip: true
                    visible: false
                    layer.enabled: true
                    layer.smooth: true

                    Image {{
                        width: root.width * {bg._OVERSCAN}
                        height: root.height * {bg._OVERSCAN}
                        x: root.imageX - cardX
                        y: root.imageY - cardY
                        source: root.blurUrl
                        fillMode: Image.PreserveAspectCrop
                        smooth: true
                        cache: true
                    }}
                }}

                Rectangle {{
                    id: regionalMask
                    anchors.fill: parent
                    radius: {bg._GLASS_RADIUS:.1f}
                    color: "white"
                    visible: false
                    layer.enabled: true
                    layer.smooth: true
                }}

                MultiEffect {{
                    id: regionalEffect
                    anchors.fill: parent
                    source: regionalSource
                    maskEnabled: true
                    maskSource: regionalMask
                    autoPaddingEnabled: false
                }}
            }}
        }}
    }}
'''
    candidate = source[:start] + regional + source[end:]
    if "id: glassMaskScene" in candidate or "ShaderEffectSource" in candidate:
        raise RuntimeError("regional candidate still contains the full-window mask path")
    for required in (
        "id: regionalGlass",
        "id: regionalSource",
        "id: regionalMask",
        "id: regionalEffect",
        "source: regionalSource",
        "maskSource: regionalMask",
    ):
        if required not in candidate:
            raise RuntimeError(f"regional candidate missing stable compositor token: {required}")
    if "layer.effect: MultiEffect" in candidate:
        raise RuntimeError("regional candidate still uses the unstable layer.effect lifecycle")
    return candidate


def _latest_new_json(output_dir: Path, before: set[Path]) -> Path:
    created = [
        path
        for path in output_dir.glob("real-gui-perf-current-*.json")
        if path not in before
    ]
    if not created:
        raise RuntimeError("real-app profiler did not produce a current JSON")
    return max(created, key=lambda path: path.stat().st_mtime_ns)


def _run_variant(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    before = set(output_dir.glob("real-gui-perf-current-*.json"))

    if args.variant == "regional":
        bg._qml_source = _regional_qml_source

    print(f"[real-glass] VARIANT={args.variant} · visual blur must remain present on every visible card")

    from tools import gui_real_app_perf as real_perf

    profiler_argv = [
        "gui_real_app_perf.py",
        "--variant",
        "current",
        "--settle",
        str(args.settle),
        "--duration",
        str(args.duration),
        "--sweep-ms",
        str(args.sweep_ms),
        "--output-dir",
        str(output_dir),
    ]
    if args.manual:
        profiler_argv.append("--manual")
    sys.argv = profiler_argv
    rc = real_perf.main()

    raw_path = _latest_new_json(output_dir, before)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    label = f"glass-{args.variant}"
    payload["glass_variant"] = args.variant
    payload["visual_gate_required"] = True
    payload["summary"]["variant"] = label
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final_path = output_dir / f"real-gui-glass-{args.variant}-{stamp}.json"
    final_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_path.unlink(missing_ok=True)

    summary = payload["summary"]
    print("\nREAL GUI GLASS PERF SUMMARY")
    print(f"variant              {label}")
    print(
        "presentation p95/p99 "
        f"{float(summary['presentation_tick_p95_ms']):.2f} / "
        f"{float(summary['presentation_tick_p99_ms']):.2f} ms"
    )
    print(
        "Quick swap p95/p99   "
        f"{float(summary['quick_swap_p95_ms']):.2f} / "
        f"{float(summary['quick_swap_p99_ms']):.2f} ms"
    )
    print(f"CPU core             {float(summary['cpu_core_percent']):.2f}%")
    print("VISUAL GATE           REQUIRED · every visible glass card must retain blur")
    print(f"JSON                 {final_path}")
    return int(rc)


def _gain(before: float, after: float) -> float:
    return (before - after) / max(1e-9, before) * 100.0


def _compare(paths: list[Path]) -> int:
    if len(paths) != 2:
        raise SystemExit("--compare requires FULL_WINDOW_JSON REGIONAL_JSON")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    old = payloads[0]["summary"]
    new = payloads[1]["summary"]
    if old.get("variant") != "glass-full-window" or new.get("variant") != "glass-regional":
        raise SystemExit("compare order must be full-window JSON then regional JSON")

    metrics = (
        ("quick_swap_p95_ms", "Quick swap p95"),
        ("quick_swap_p99_ms", "Quick swap p99"),
        ("presentation_tick_p99_ms", "presentation p99"),
        ("cpu_core_percent", "CPU core"),
    )
    print("\nREAL GUI GLASS A/B · regional vs full-window")
    gains: dict[str, float] = {}
    for key, label in metrics:
        before = float(old[key])
        after = float(new[key])
        gain = _gain(before, after)
        gains[key] = gain
        print(f"{label:<20} {before:8.2f} -> {after:8.2f}  ({gain:+6.1f}%)")

    weighted = gains["quick_swap_p95_ms"] * 0.4 + gains["quick_swap_p99_ms"] * 0.6
    if weighted >= 8.0 and gains["presentation_tick_p99_ms"] > -8.0:
        verdict = "REGIONAL PERF CANDIDATE"
    elif weighted <= 2.0:
        verdict = "KEEP FULL-WINDOW"
    else:
        verdict = "INCONCLUSIVE"
    print(f"weighted Quick gain   {weighted:+.1f}%")
    print(f"VERDICT: {verdict}")
    if verdict == "REGIONAL PERF CANDIDATE":
        print("VISUAL GATE: REQUIRED · do not promote unless every visible card keeps blur for the full run")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real Listing Studio A/B for full-window vs regional glass composition"
    )
    parser.add_argument("--variant", choices=VARIANTS, default="full-window")
    parser.add_argument("--settle", type=float, default=5.0)
    parser.add_argument("--duration", type=float, default=18.0)
    parser.add_argument("--sweep-ms", type=int, default=70)
    parser.add_argument("--output-dir", type=Path, default=Path("perf_results"))
    parser.add_argument("--manual", action="store_true")
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("FULL_WINDOW_JSON", "REGIONAL_JSON"),
    )
    args = parser.parse_args()
    if args.compare:
        return _compare(args.compare)
    return _run_variant(args)


if __name__ == "__main__":
    raise SystemExit(main())
