# GUI Card Performance Lab

这个实验室只用于 Windows 本机性能对比，**不会被 `run_local_gui.py` 导入，也不会改变正式 GUI**。

第一轮已经完成：`baseline_frozen` 在用户真实 Windows + Qt + 显卡环境里击败了 `snapshot_gl`、`snapshot_cpu` 和 `cached_levels`。因此第二轮不再继续堆 GPU overlay / snapshot 架构，而是把当前生产冠军作为中心，研究它内部还有没有更轻、更稳的实现。

## 第二轮候选

- `baseline_frozen`：正式生产 `_CardScaleEffect`，transition 开始冻结一次 QWidget composite，然后 QPainter 缩放。所有结果以它为基准。
- `frozen_target_rect`：仍然只冻结一次 composite，但不走 `translate → scale → drawPixmap`，改成直接算目标 `QRectF` 后 `drawPixmap(targetRect, ...)`。
- `frozen_transform`：同一个 frozen composite，改成显式 `QTransform` 路径。
- `frozen_fast`：关闭 SmoothPixmapTransform 的性能上限对照。默认没有最终候选资格，除非以后重新定义视觉标准。
- `quantized_12`：仍使用生产 baseline renderer，但把 1.00~1.02 的 motion progression 量化为 12 个位置，测试减少无效 repaint 是否值得。必须严格过视觉 parity。
- `baseline_60hz` / `baseline_72hz` / `baseline_90hz`：只改变卡片 motion cadence，不改 renderer，用来找本机最稳的更新频率。
- `no_scale_control`：不做缩放，只测事件循环和卡片本体的理论下限，不作为最终候选。

第一轮已经判输的 `snapshot_gl / snapshot_cpu / cached_levels` 已从主 harness 删除。这样也从根上删除了旧 `_PaintGate` 生命周期问题：不再存在 `setGraphicsEffect(None)` 后又对已删除 C++ effect 调 `deleteLater()` 的路径。

## 两种场景

`single`：一张复杂卡片，保持和第一轮一样的输入框、按钮、表格、日志负载，重点看单 transition 的 p95 / p99 / prepare / start-gap。

`crossover`：六张复杂卡片，自动按 `A → B → C → D → E → F → E → D → C → B` 高速横穿。每次切换只保留当前相关的最多两张 motion，专门模拟真实 GUI 中鼠标快速连续跨卡片时最容易出现的顿挫。

`both`：同一轮里两个场景都跑，是第二轮推荐模式。

## 自动记录指标

每个策略、每一轮、每个场景都会记录：

- frame median / p95 / p99 / max
- 1.5× / 2× frame budget 的 long-frame rate
- transition / crossover 开始后的首帧间隔 p95
- transition prepare p95
- tick work p95
- CPU time / wall time
- target Hz、显示器刷新率、分辨率、DPR、Qt / PySide 版本

## Claude 本机标准执行

先同步当前 `feat/local-test-gui`，不要修改正式 GUI。使用项目现有 GUI Python 环境。

推荐直接跑第二轮完整矩阵：

```powershell
$env:PYTHONPATH=(Get-Location).Path
python tools/gui_card_perf_lab.py --scenario both --profile large --rounds 5 --warmup-cycles 2 --cycles 8 --output-dir perf_results
```

如果 large 差距仍然很小，再补 heavy：

```powershell
python tools/gui_card_perf_lab.py --scenario both --profile huge --rounds 5 --warmup-cycles 2 --cycles 6 --output-dir perf_results
```

先自动排名：

```powershell
python tools/analyze_gui_card_perf.py perf_results\gui-card-perf-*.json
```

分析器现在有一个额外保护：**挑战者至少要在综合 score 上领先 baseline 2% 才值得替换正式架构**。不到 2% 默认按 benchmark noise / 收益不足处理，保留 `baseline_frozen`，避免为了 0.x% 的差距增加生产复杂度。

## 同屏视觉与交互 parity

不要再开三个窗口来回比较。对真正明显领先的候选，用同一个动画 clock 左右并排：

```powershell
python tools/gui_card_perf_lab.py --compare-demo frozen_target_rect --profile large
python tools/gui_card_perf_lab.py --compare-demo frozen_transform --profile large
python tools/gui_card_perf_lab.py --compare-demo quantized_12 --profile large
```

左侧永远是 `baseline_frozen`，右侧是 candidate。两边同一时间收到同一个 scale progression。

候选必须满足：

1. 文字、输入框、按钮、表格和卡片一起缩放，中心一致。
2. `1.00 → 1.02 → 1.00` 无跳变、闪白、残影、异常模糊、裁剪或边缘抖动。
3. 动画期间输入框可聚焦/输入，按钮 hover/press 正常。
4. 字体、边缘清晰度与 baseline 肉眼一致。
5. `quantized_12` 如果能肉眼看到级进/顿挫，直接 FAIL。

写 `perf_results/parity.json`，例如：

```json
{
  "baseline_frozen": true,
  "frozen_target_rect": true,
  "frozen_transform": true,
  "quantized_12": false,
  "baseline_60hz": true,
  "baseline_72hz": true,
  "baseline_90hz": true
}
```

最终分析：

```powershell
python tools/analyze_gui_card_perf.py perf_results\gui-card-perf-*.json --parity perf_results\parity.json --write perf_results\recommendation.json
```

只有同时满足：

- parity PASS
- 不是 reference-only
- 综合 score 明确超过 baseline 的最小替换收益门槛

才允许成为 `FINAL CANDIDATE`。否则保留当前 baseline。

## Claude 应回传

- large / huge 原始 JSON 与 CSV
- analyzer 完整输出
- `parity.json`
- `recommendation.json`
- Windows 刷新率 / 分辨率 / DPR
- 如果 FAIL，写清楚是字体、边缘、卡片 clipping、动画量化、输入还是按钮交互问题

## 可直接给 Claude 的任务说明

> 同步 `feat/local-test-gui` 最新版。不要修改正式 GUI。设置仓库根目录到 `PYTHONPATH`，按照 `docs/GUI_CARD_PERF_LAB.md` 跑第二轮。先用 `--scenario both --profile large --rounds 5 --warmup-cycles 2 --cycles 8`，必要时再跑 huge。运行 analyzer。只有明显超过 baseline 2% 的方案才进入 `--compare-demo` 同屏 parity；视觉或交互任何不一致都 FAIL。写 parity.json，再生成 recommendation.json。把所有 JSON/CSV、终端输出和 parity 结论返回给 ChatGPT，不要自行改正式 GUI。
