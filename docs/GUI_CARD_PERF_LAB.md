# GUI Card Performance Lab

这个实验室只用于 Windows 本机性能对比，不会被 `run_local_gui.py` 导入，也不会改变正式 GUI。

当前结论已经收敛：

- 正式 renderer 基线仍是 `baseline_frozen` / `_CardScaleEffect`。
- 第一轮的 `snapshot_gl`、`snapshot_cpu`、`cached_levels` 已判输并退出主实验。
- 第二轮发现真正的卡顿集中在 **多卡高速 crossover**：连续跨卡时 p95 / p99 明显恶化。
- `baseline_60hz / 72hz / 90hz` 只是 cadence 实验，不是 renderer 架构，禁止再作为 renderer winner。

## 当前 focused 目标

只确认一个问题：

> `frozen_target_rect` 能否在完全相同 target Hz 下，稳定降低多卡 crossover 的 p95 / p99，并且不造成不可接受的 transition start-gap 回归？

因此下一轮只跑：

- `baseline_frozen`
- `frozen_target_rect`

不再跑其他 renderer。

## 最快的标准执行

先同步：

```powershell
git pull
$env:PYTHONPATH=(Get-Location).Path
```

### 1. large crossover

```powershell
python tools/gui_card_perf_lab.py `
  --strategies baseline_frozen,frozen_target_rect `
  --scenario crossover `
  --profile large `
  --rounds 8 `
  --warmup-cycles 2 `
  --cycles 8 `
  --output-dir perf_results
```

### 2. huge crossover

```powershell
python tools/gui_card_perf_lab.py `
  --strategies baseline_frozen,frozen_target_rect `
  --scenario crossover `
  --profile huge `
  --rounds 6 `
  --warmup-cycles 2 `
  --cycles 6 `
  --output-dir perf_results
```

这两轮加起来只有 28 个 strategy-run，远小于上一轮 90 run。不要再跑 9 策略矩阵。

### 3. 分析

只把这次 focused JSON 传给 analyzer，不要把 round-1 / round-2 的旧 glob 混进来：

```powershell
python tools/analyze_gui_card_perf.py `
  perf_results\<large-focused-json> `
  perf_results\<huge-focused-json> `
  --write perf_results\focused-recommendation.json
```

Analyzer 现在遵守：

- `baseline_60hz / 72hz / 90hz` 单独报告，永远不能成为 renderer winner。
- 只有相同 target Hz 的数据才参与 renderer 比较。
- crossover 权重 80%，single 权重 20%。
- focused crossover-only 运行可以直接用于筛选候选。
- challenger 至少比 baseline 综合改善 2% 才值得继续。
- transition start-gap 最多允许回归 15%；超过直接失去资格。

## 视觉确认

只有 analyzer 输出 `FOCUSED CANDIDATE: frozen_target_rect` 时才开：

```powershell
python tools/gui_card_perf_lab.py --compare-demo frozen_target_rect --profile large
```

同屏左侧是 baseline，右侧是 candidate，使用同一个动画 clock。

必须检查：

1. 文字、输入框、按钮、表格一起缩放。
2. 中心点、边缘和 clipping 与 baseline 一致。
3. 1.00 → 1.02 → 1.00 无跳变、闪白、残影。
4. 输入框 focus、按钮 hover/press、鼠标完全正常。

如果一致，写：

```json
{
  "baseline_frozen": true,
  "frozen_target_rect": true
}
```

然后：

```powershell
python tools/analyze_gui_card_perf.py `
  perf_results\<large-focused-json> `
  perf_results\<huge-focused-json> `
  --parity perf_results\parity.json `
  --write perf_results\focused-recommendation.json
```

只有输出 `FINAL BENCHMARK CANDIDATE: frozen_target_rect`，才值得做一次正式 Listing Studio A/B。

如果输出 `KEEP BASELINE`，到这里停止，不再继续磨 renderer。

## Claude 回传

只需要：

- large focused JSON / CSV
- huge focused JSON / CSV
- analyzer 完整输出
- `focused-recommendation.json`
- 如果进入视觉确认，再加 `parity.json`
- 一句话说明 compare-demo 是否完全一致

不要修改正式 GUI。
