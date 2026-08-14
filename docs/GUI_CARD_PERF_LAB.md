# GUI Card Performance Lab

这个实验室只用于 Windows 本机性能对比，**不会被 `run_local_gui.py` 导入，也不会改变正式 GUI**。

目标不是继续凭感觉调参数，而是在同一台机器、同一张代表性复杂卡片、完全一致的 `1.00 ↔ 1.02 / 300 ms / cubic-bezier` 动画上，对多个渲染结构做重复 A/B 数据采集。

## 当前包含的方案

- `baseline_frozen`：直接使用正式代码里的 `_CardScaleEffect`。这是当前生产基线：transition 开始冻结一次 QWidget composite，之后 QPainter 缩放。
- `live_effect`：每次 draw 都重新 `sourcePixmap()` 的负面对照。只用于证明重复 raster 的成本，不作为候选。
- `snapshot_cpu`：transition 开始 `frame.grab()` 一次，透明 QWidget overlay 每帧缩放快照；真实 card 保留输入/布局。
- `snapshot_gl`：与前者相同，但 overlay 是 `QOpenGLWidget`。这就是之前实机“反而更卡”的路线，保留进实验室作为可测量参考，不再直接进入正式 GUI。
- `cached_levels`：每次 transition 抓一次，然后预生成 17 个 1.00~1.02 缩放级别；动画只画最近的缓存图，不做每帧 SmoothPixmapTransform。需要严格过视觉一致性门槛。
- `no_scale_control`：完全不缩放，只测事件循环和卡片本体的理论下限，不作为最终候选。

## 自动记录的指标

每个方案、每一轮都会记录：

- frame median / p95 / p99 / max
- 超过 1.5× frame budget 的 long-frame 数量和比例
- 超过 2× frame budget 的严重 long-frame 数量和比例
- transition 开始后的首帧间隔 p95（最容易对应“鼠标刚进入卡片时顿一下”）
- transition prepare p95（例如 `frame.grab()`、缓存级别生成的同步成本）
- 每 tick 同步工作耗时
- Python + Qt 进程 CPU time / wall time（单核百分比口径）
- Qt / PySide / 分辨率 / DPR / 刷新率等环境信息

实验顺序每轮都会轮转，减少“永远第一个/最后一个方案”的缓存与热机偏差。

## Claude 本机标准执行流程

先同步当前 `feat/local-test-gui`，不要修改正式 GUI。使用项目现有 GUI venv。

第一轮正式数据：

```powershell
python tools/gui_card_perf_lab.py --strategies all --profile large --rounds 5 --warmup-cycles 2 --cycles 10 --output-dir perf_results
```

如果 large 已经把差距拉开，再补一次更重负载：

```powershell
python tools/gui_card_perf_lab.py --strategies all --profile huge --rounds 5 --warmup-cycles 2 --cycles 10 --output-dir perf_results
```

先做无人工门槛的初筛：

```powershell
python tools/analyze_gui_card_perf.py perf_results\gui-card-perf-*.json
```

它只会给 `PROVISIONAL LEADER`，不能直接认定最终架构。

## 视觉与交互一致性门槛

自动数据最好的 2~3 个方案必须逐个进入 demo：

```powershell
python tools/gui_card_perf_lab.py --demo baseline_frozen --profile large
python tools/gui_card_perf_lab.py --demo snapshot_cpu --profile large
python tools/gui_card_perf_lab.py --demo snapshot_gl --profile large
python tools/gui_card_perf_lab.py --demo cached_levels --profile large
```

每个候选必须确认：

1. 文字、输入框、按钮、表格与卡片本体一起缩放，中心点与正式基线一致。
2. `1.00 → 1.02 → 1.00` 没有跳变、闪白、残影、模糊异常或边缘抖动。
3. 动画循环过程中输入框仍可聚焦/输入，按钮 hover/press 正常，鼠标没有异常覆盖。
4. card 边缘和控件文字没有明显锯齿、像素级量化跳动。
5. 如果某方案肉眼与 baseline 不一致，**无论性能多高都直接 FAIL**。

把结果写成，例如 `perf_results/parity.json`：

```json
{
  "baseline_frozen": true,
  "snapshot_cpu": true,
  "snapshot_gl": false,
  "cached_levels": true
}
```

然后做最终排名：

```powershell
python tools/analyze_gui_card_perf.py perf_results\gui-card-perf-*.json --parity perf_results\parity.json --write perf_results\recommendation.json
```

只有通过 parity 的方案才有资格成为 `FINAL CANDIDATE`。

## 选择规则

不要只看平均帧时间。对这个 GUI，优先级应该是：

1. 视觉 / 鼠标 / focus 完全一致，失败直接淘汰。
2. `frame_p95` 和 `frame_p99`，这是实际“跟手感”的主要指标。
3. `transition_start_gap_p95`，这是进入/离开大卡片瞬间是否顿一下。
4. long-frame rate，尤其连续快速跨卡片时最明显。
5. CPU；如果两个方案帧时间接近，选 CPU 更低、结构更简单的。
6. `transition_prepare_p95`；不能为了低 steady-state 把一次巨大卡顿塞到动画起点。

一个方案只有“平均更快”但 p99/start-gap 更差，应当淘汰。

## Claude 应该回传给 ChatGPT 的内容

不要只说“X 更快”。请完整返回：

- 两个原始 JSON 文件（large / huge，如果都跑）
- CSV
- `parity.json`
- `recommendation.json`
- analyzer 的完整终端输出
- 如果某方案视觉 FAIL，说明具体是闪烁、字体缩放、输入、鼠标、边缘还是卡片 clipping 问题
- Windows 显示刷新率、分辨率、DPR；实验期间不要改系统缩放比例

拿到这些数据之后，ChatGPT 再只把胜出的结构集成到正式 GUI，并做一次真实 Listing Studio A/B。没有胜出就保留当前 baseline，不继续堆架构。

## 可直接给 Claude 的任务说明

> 你只负责在我的 Windows 本机运行 `tools/gui_card_perf_lab.py` 和 `tools/analyze_gui_card_perf.py`，不要修改正式 GUI 代码。先按文档跑 large 5 轮；有时间再跑 huge 5 轮。保存所有 JSON/CSV。根据 analyzer 选前 2~3 名逐个用 `--demo` 检查视觉、输入框、按钮、鼠标和缩放一致性，写 `perf_results/parity.json`，再带 `--parity` 做最终排名并生成 `recommendation.json`。最后把原始结果和终端输出完整返回，不要自行把实验方案合并进正式 GUI。
