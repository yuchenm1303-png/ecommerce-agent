# ecommerce-agent

电商卖家后台批量信息采集、匹配、填写与校验自动化原型。

项目已经从本地 `mock_site` 进入真实平台适配阶段：**Makro Marketplace Seller Center**。

核心目标：

**读取商品资料 → 打开 Add Listing → 动态抓取页面问题 → 从证据中解析可靠答案 → 自动填写 → 二次校验 → 人工/规则安全门 → 保存 → 记录日志**

## 当前架构

系统刻意分成两层，避免让 AI 直接操作页面或猜经营数据：

1. **Dynamic Field Discovery / Browser Execution**
   - Playwright 连接一个长期运行的 Edge/CDP 会话。
   - 运行时扫描当前 Makro listing 的真实字段、下拉选项、单位、required 状态。
   - 不写死类目字段总数。
   - 填写后立即回读 + React settled readback；关键流程还会 Save 后重新打开验证。

2. **Evidence-grounded Answer Resolver**
   - QA Excel 是问题清单，不要求每一行预先有答案。
   - 每条自动答案必须带来源、source reference、evidence text、confidence 和 provenance。
   - 来源冲突、低置信度、无精确下拉选项、GTIN/字段约束失败都会被阻止。
   - SKU、价格、MOQ、履约、发货、区域等经营字段只能来自 structured/business/config/rule，不能由图片/网页/AI 猜测。

## Answer Resolver V2

### 客户 QA 清单

支持 `.csv / .xlsx / .xlsm`，自动在前 50 行寻找真实表头，并保留：

- 编号
- 问题
- 问题说明
- 问题类别
- 选项
- 单位
- 答案
- 来源工作表/行号

答案为空的行仍然保留为待解析问题。

### 证据输入

当前确定性管道支持：

- 结构化商品/经营数据表
- 客户 QA 中已经确认的答案
- 人工/确定性 `facts.json`
- 明确 `key: value` 补充文本
- 严格 `EvidencePacket`（供图片、文档、网页、AI extractor 使用）
- supplier / official 页面 `SourceSnapshot` 中的显式 table / JSON-LD 参数

图片/网页/AI 结果不能直接进入 resolver，必须先通过 EvidencePacket 校验：

- 与当前 QA 问题一一对应
- 不得注入未请求的通用属性
- 不得提供经营字段
- 必须有 `source_reference`
- 必须有 `evidence_text`
- 必须有 `confidence`
- 若已有 SKU / Model / Brand 身份锚点，必须匹配当前商品

### 来源置信度上限

模型自己报 `0.99` 并不能获得 0.99 的系统信任度。每个 source type 有独立 confidence ceiling。例如 `ai_synthesis` 的上限低于默认自动填写阈值，因此 AI 推理可以进入 review，但不能凭自己的置信度直接授权浏览器写入。

### 冲突与值校验

Resolver 会保留真实来源冲突，不按来源优先级强行覆盖。只对机械等价表示做保守归一化，例如：

- `3 inch` == `3.0 inches`
- `1920 x 1080` == `1920×1080`

但不会擅自认为：

- `1080P` == `1920x1080`
- `3.0 inch` == `3.16 inch`

写入前还有确定性校验：

- EAN / GTIN checksum
- 数值字段 min / max
- maxlength
- Selling Price <= Base Price/MRP
- MinOQ <= MaxOQ

### 主要命令

生成 QA 解析报告（不打开 Makro）：

```powershell
python makro_resolve_product.py --qa <qa.xlsx> [evidence options]
```

输出：

- `resolution.json`
- `resolution.xlsx`
- `evidence-manifest.json`

捕获供应商/官方商品页面（使用与 Makro 隔离的独立 source Edge，默认 CDP 9333）：

```powershell
python makro_capture_source.py --url <product-url>
```

如果页面要求 CAPTCHA / 人机验证，脚本会停止，不做绕过，并保持 source Edge 打开供人工正常处理。

把 snapshot 中明确的 table / JSON-LD 参数转成 EvidencePacket：

```powershell
python makro_extract_snapshot.py --qa <qa.xlsx> --snapshot <source-snapshot.json>
```

只读扫描当前 Makro 页面并生成 READY/BLOCKED 填写计划：

```powershell
python makro_plan_listing.py --qa <qa.xlsx> --expected-vertical <vertical> [evidence options]
```

该命令不填写、不 Save、不 Send to QC。

## 安全原则

- 宁可漏填，不要错填。
- 不写死 Makro 类目字段数量。
- 多个 Add Listing 标签页时 fail closed。
- 商品身份冲突时 fail closed。
- 下拉选项只接受唯一精确匹配。
- 经营字段拒绝 AI / image / web 来源。
- CAPTCHA / 风控只允许人工正常处理，不自动绕过。
- Makro 长期 Edge 与 source Edge 使用不同 profile / CDP port。
- 最终 `Send to QC` 始终是独立的高风险提交动作，不与解析/测试隐式绑定。
