# ecommerce-agent

Makro Marketplace Seller Center 的 AI-first 商品资料补全、字段决策、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → Product Source Pack → 一次整商品 AI 本地填空 → 必要时一次有来源联网补空 → Thin Hard Guards → Fill Plan → 浏览器填写 → Save → reopen persisted verify → Product Photos persistence**

`Send to QC` 当前始终禁止自动点击。

## 核心原则

### AI 负责商品语义

商品语言理解、翻译、同义词、计数、规格含义、字段映射、来源综合、冲突判断都交给 AI。

不要重新增加本地商品语义规则，例如：颜色别名、双镜头计数、G-Sensor、FOV / Vehicle Brand / SD Card / Camera Type marker 表、QA alias/matcher、deterministic synthesis product rules。

### Python 只负责硬边界

本地只保留机械规则：live schema / field id / product identity / source provenance、seller-operated business field lock、Makro option / qualifier / multi-value 控件形态、GTIN checksum、numeric min/max、maxlength、Selling Price <= Base Price/MRP、MinOQ <= MaxOQ、DOM 唯一定位、React readback、Save/reopen persistence，以及禁止自动 `Send to QC`。

## 唯一目标 Schema

Makro 当前页面发现的 live schema 是 AI 唯一目标字段集合。客户 Excel/QA 是商品资料来源，不再是另一套待匹配问题 schema。

## Product Source Pack

第一遍 AI 一次看到客户 workbook/context、selected variant / SKU、explicit facts/product table、商品图片、supplier/official snapshot，以及当前 Makro live fields/options/units/required/section。

图片和网页资料不再先转换为本地 semantic facts；AI直接理解原始 grounded sources。

## 本地 AI 填空

`makro_resolve_ai.py` 是唯一生产 AI Resolver。

正常路径：**一个商品 + 全部本地资料 + 全部 live fields = 一次 multimodal model call**。

Qwen3.5 Omni 生产配置自动使用：

- OpenAI-compatible streaming profile；
- `response_format={"type":"json_object"}`；
- thinking disabled；
- JSON mode 不设置 `max_tokens`，避免完整 JSON 被输出上限截断；
- 默认 `--max-repair-attempts 0`，结构输出失败不会自动把整商品和图片再发一遍；
- `--request-timeout-seconds` 是整个阶段的 wall-clock deadline，而不只是底层 read timeout；
- 每 15 秒打印进度，并报告 connection / first output / complete 时间。

模型输出 contract 已压缩。模型仍对每个 live target 给出一个简短 decision，以保证完整覆盖，但不再回显 product identity、schema digest、source manifest digest、contract metadata 或本地 warnings。这些由 Python 自己附回最终 packet。正常 READY 只需要 value + 最小 citation；只有冲突才需要 alternatives，只有值得外查时才需要 search_queries。

最终持久化 decision packet 状态只有 `ready / review / conflict / missing / business_locked`。

## 联网补空

第一遍本地资料不足时，AI可以给 unresolved 字段生成少量 `search_queries`。使用 DashScope Qwen 时，系统最多追加一次 sourced web enrichment，所有需要研究的字段一起处理。

联网阶段复用 `DASHSCOPE_API_KEY`，使用 `search_strategy=agent`、`enable_source=true` 和 JSON mode，并受同样 wall-clock deadline 限制。READY 和 seller business fields 冻结；web URL 必须真实出现在本次 DashScope search sources 中，编造 URL 不能授权 READY。联网失败保留第一遍有效本地结果，不进入 Agent 循环。

正常调用预算：本地资料足够为 1 call；需要联网为 1 local + 1 web；相同输入 hot cache 可 0 call。

## Hard Guards / Fill Plan

`app/fill_plan.py` 不解释商品含义，只把 AI decisions 转成可执行计划。

- AI READY + hard guards pass → `READY`
- AI REVIEW → blocked，可在明确人工 review 模式下作为 preview candidate
- AI CONFLICT → blocked
- AI MISSING → blocked
- business field → 只接受 explicit seller data

Price、Stock、MOQ、Fulfilment、Shipping 等不允许图片、供应商网页、普通 web search 或 AI 推理生成。

## 浏览器执行层

浏览器层保留 live field discovery、text/textarea/dropdown/number/qualifier/multi-value、React settled readback、section Save、Save 后 reopen persisted verification、Product Photos persistence、schema/source/identity drift fail closed，以及禁止自动 Send to QC。

## 正确运行顺序

### 1. 首次只读扫描 live schema

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

### 2. AI 本地填空 + 可选联网补空

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 `
  --model qwen3.5-omni-plus `
  --api-key-env DASHSCOPE_API_KEY `
  --qa <qa.xlsx> `
  --live-schema <live-schema.json> `
  --sku <sku> `
  --image <img1> `
  --image <img2> `
  --supplier-snapshot <snapshot.json> `
  --disable-thinking `
  --web-enrich auto `
  --request-timeout-seconds 120
```

Qwen Omni 在 `--structured-mode auto` 下自动使用 JSON mode。主要输出：`ai-decisions.json`、触发 web 时的 `ai-decisions.local.json`、`search-requests.json`、`web-search-sources.json`、`source-manifest.json`、`run-manifest.json`。

### 3. 最终只读 Fill Plan

```powershell
python makro_plan_listing.py `
  --decision-packet <ai-decisions.json> `
  --qa <qa.xlsx> `
  --live-schema <same-live-schema.json> `
  --sku <sku> `
  --supplier-snapshot <same-snapshot.json> `
  --image <same-img1> `
  --image <same-img2> `
  --expected-vertical vehicle_camera_system
```

### 4. 人工检查后再执行真实 Step 3 persistence acceptance

```powershell
python makro_preview_listing.py `
  --qa <qa.xlsx> `
  --decision-packet <ai-decisions.json> `
  --live-schema <same-live-schema.json> `
  --sku <sku> `
  --supplier-snapshot <same-snapshot.json> `
  --image <same-evidence-img1> `
  --image <same-evidence-img2> `
  --expected-vertical vehicle_camera_system `
  --all-step3 `
  --allow-section-save `
  [--upload-image <listing-image>]
```

`--image` 只是 evidence；只有 `--upload-image` 会上传 Product Photos。

## 安全不变量

复用已登录长期 Edge/CDP；多 Add Listing tabs、vertical/schema/source/identity 不一致、已有用户未保存 section 都 fail closed；不覆盖当前非-placeholder 用户值；option/qualifier/slot 不满足时写入前失败；React readback 不一致不算 validated；Save 后 reopen 不一致不算 persisted；Product Photos staged 不等于 persisted；`Send to QC` 始终禁止。

## 关键文件

- `app/ai_decisions.py`：compact AI field-decision contract、provenance validation、whole-product cache
- `app/product_context.py`：canonical Product Source Pack context
- `app/business_fields.py`：seller-operated field policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：AI decisions → executable Fill Plan
- `app/semantic_grounding.py`：原始 image/text source manifest/citations
- `app/providers/openai_compatible.py`：Qwen/compatible JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：一次有来源 web enrichment + wall deadline
- `makro_plan_listing.py`：live-schema scan + final read-only Fill Plan
- `makro_resolve_ai.py`：唯一生产 AI Resolver
- `makro_preview_listing.py`：真实 Step 3 browser acceptance

旧本地商品语义主链已经删除：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、attribute-specific deterministic synthesis、snapshot→semantic-fact mapping。不要恢复兼容 wrapper。

## 开发验收

修改后至少要求 `pytest -q`、GitHub Actions tests、mock-e2e、browser dry-run/probe 全部通过。真实 Makro/Qwen 最终由用户本机环境验证；真实写入前必须先检查 AI Decisions + read-only Fill Plan；PR 保持 Draft，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。
