# ecommerce-agent

Makro Marketplace Seller Center 的 AI-first 商品资料补全、字段决策、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → Product Source Pack → Product Profile → 小批字段并行映射 → unresolved 并行 Web Research → 一次 text-only Final Resolve → Thin Hard Guards → Fill Plan → Browser → Save/reopen verify → Product Photos persistence**

`Send to QC` 当前始终禁止自动点击。

## 核心原则

AI 负责商品理解、翻译、同义词、计数、规格语义、字段映射、多来源综合和冲突判断。本地 Python 不维护颜色、双镜头、G-Sensor、FOV、Vehicle Brand、SD Card、Camera Type、cabin/rear、包装尺寸等商品语义规则，也不恢复 QA matcher / alias / deterministic synthesis。

Python 只负责机械边界：调度/并发/cache、live schema / field id / product identity / source provenance、seller-operated business lock、Makro option / qualifier / multi-value 控件形态、GTIN checksum、numeric min/max、maxlength、Selling Price <= Base Price/MRP、MinOQ <= MaxOQ、DOM 唯一定位、React readback、Save/reopen persistence，以及禁止自动 `Send to QC`。

## 为什么不再一次回答 77 个字段

旧架构把原始图片、客户资料、supplier snapshot 和全部 Makro live fields 塞进一个巨大 multimodal JSON 请求。真实 M8 已证明这个形态不稳定：快模型能完成但语义质量下降，强模型可能超过 120 秒仍无法完成完整 JSON。

当前架构不再优化“调用次数最少”，而优化 **wall time、可局部重试、cache 粒度和语义质量**：

1. 图片/原始资料只理解一次；
2. 字段映射只读取 compact Product Profile；
3. live fields 按顺序机械切成小 batch 并行处理；
4. Web 只处理 unresolved；
5. Web 先找证据，最终决策再由 text-only resolver 完成；
6. 任意 field/web batch 失败只影响该 batch，不重跑整商品。

## Stage 1 — Product Profile

`app/product_profile.py` 用一次 multimodal 请求阅读客户 workbook/context、selected variant / SKU、explicit facts、商品图片、supplier/official snapshot。

这一阶段 **完全看不到 Makro 77 个目标字段**。它只生成 compact 商品事实，例如 identity、selected variant、产品/包装 scope、规格、功能和多来源 conflict。

要求：

- unknown fact 直接省略；
- 同一属性/同一 scope 的可信来源冲突必须保留多个 candidates；
- packaging 与 product body、cabin/interior 与 rear/back、manual language 与 UI language、产品 brand 与兼容 vehicle brand 分开；
- No/False/Not included 必须有显式负面证据；
- citation 必须回到原始 source id。

Product Profile cache 只依赖商品资料、identity、模型和 profile contract，**不依赖 Makro schema**。因此 Makro 增删字段不会迫使系统重新看图片。

## Stage 2 — Parallel Field Mapping

`app/field_mapping.py` 只接收 compact Product Profile 和 live Makro fields，不再接收图片或原始大文本。

非经营字段按 live schema 顺序机械分组，默认：

- `--field-batch-size 12`
- `--field-concurrency 4`

这只是负载调度，不包含“camera/storage/dimension”之类本地语义分类。

多个小 batch 并行请求。单个 batch 失败时，该 batch 字段保持 MISSING/REVIEW，其他 batch 正常继续。每个 batch 独立 content-addressed cache。

## Stage 3 — Parallel Web Research

只有 `MISSING / REVIEW / CONFLICT` 的非经营字段进入 Web。

默认：

- `--web-batch-size 5`
- `--web-concurrency 3`
- `--web-search-model qwen3.7-max`

Web 使用 OpenAI-compatible Responses API `web_search`。这一阶段只收集 evidence，不直接决定 READY。URL 只有真实存在于本次 `web_search_call.action.sources` 才能进入 evidence；模型编造 URL 会被丢弃。

每个 research batch 独立 cache，某批搜索失败不会破坏 local decisions。

## Stage 4 — Final Resolve

只有实际获得 Web evidence 的 unresolved fields 才进入一次 text-only Final Resolve。

输入只有：

- Product Profile；
- 这些 unresolved fields；
- 已验证 Web evidence。

不会重新发送图片，不会重新回答已 READY 字段。最终状态仍只有：

`ready / review / conflict / missing / business_locked`

## 模型

默认本地商品理解、字段映射和 Final Resolve 使用：

`qwen3.7-plus`

默认 Web Research 使用：

`qwen3.7-max`

本地 JSON task 使用 `json_object`、thinking disabled、真实 wall-clock deadline。Web Search 不强行发送与 search tool 不兼容的 `response_format=json_object`，而是解析 Responses 输出的 JSON text，并独立校验 search sources。

模型职责固定，不通过不断换模型掩盖架构问题。

## Cache

现在有四层独立 cache：

1. Product Profile cache；
2. Field batch cache；
3. Web research batch cache；
4. Final Resolve cache。

相同商品热运行应尽量达到 0 model calls。若只变更少量 live fields，只需重跑受影响 field batch；原始图片不重传。

## Hard Guards / Fill Plan

`app/ai_decisions.py` 现在只保存 decision 数据结构、field/schema/source digest、citation validation 和 packet I/O，不再包含模型执行器。

`app/fill_plan.py` 不解释商品含义，只把 AI decisions 转成执行计划：

- AI READY + hard guards pass → `READY`
- AI REVIEW / CONFLICT / MISSING → blocked
- business field → 只接受 explicit seller data

Price、Stock、MOQ、Fulfilment、Shipping 等不允许图片、supplier、普通 web search 或 AI 推理生成。

## 浏览器执行层

浏览器层保持不变：live field discovery、text/textarea/dropdown/number/qualifier/multi-value、React settled readback、section Save、Save 后 reopen persisted verification、Product Photos persistence、schema/source/identity drift fail closed，以及禁止自动 Send to QC。

## 运行顺序

### 1. 扫描 live schema

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

### 2. 四阶段 AI Resolver

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 `
  --model qwen3.7-plus `
  --web-search-model qwen3.7-max `
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

主要输出：

- `product-profile.json`
- `ai-decisions.local.json`
- `search-requests.json`
- `web-evidence.json`
- `web-search-sources.json`
- `ai-decisions.json`
- `source-manifest.json`
- `run-manifest.json`

`run-manifest.json` 分别记录 Product Profile、field mapping、Web Research、Final Resolve 的 calls / cache hits / batch count / elapsed。

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

### 4. 人工检查后再进入真实 persistence acceptance

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

- `app/product_profile.py`：一次 raw multimodal 商品理解 + profile cache
- `app/field_mapping.py`：compact profile → 小批 live fields 并行映射 + batch cache
- `app/web_enrichment.py`：unresolved 并行 Web Research + text-only Final Resolve
- `app/ai_decisions.py`：decision 数据/provenance/schema/source validation
- `app/product_context.py`：canonical Product Source Pack context
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：AI decisions → executable Fill Plan
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/providers/openai_compatible.py`：Qwen/compatible JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：Responses web_search + source provenance + wall deadline
- `makro_resolve_ai.py`：唯一 AI orchestration entrypoint
- `makro_plan_listing.py`：schema scan + read-only planner
- `makro_preview_listing.py`：真实 browser acceptance

旧本地商品语义主链和旧超级请求执行器已经删除：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、attribute-specific deterministic synthesis、snapshot→semantic-fact mapping、`run_ai_resolution()` whole-product field resolver。不要恢复兼容 wrapper。

## 验收

修改后至少要求 `pytest -q`、GitHub Actions tests、mock-e2e、browser dry-run/probe 全部通过。

真实 M8 下一轮重点不再验证“一个 77-field call 能否撑住”，而验证：

- Product Profile 是否一次正确理解原始资料；
- 3.0/3.16、720p/1080p 是否在 profile 阶段保留 conflict；
- front+cabin、package/product scope、negative evidence 是否正确；
- field batches 并行 wall time；
- unresolved Web Research 是否返回真实 URL/evidence；
- hot run 四层 cache 是否接近 0 calls；
- 最终 read-only Fill Plan coverage/安全性。

真实写入前必须先检查 `product-profile.json + ai-decisions.json + Fill Plan`。PR 保持 Draft，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。