# ecommerce-agent

Makro Marketplace Seller Center 的 AI-first 商品资料补全、字段决策、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → Product Source Pack → Product Profile → Local Fill（现有资料先填）→ Web Fill（只搜仍为空的字段并直接补入）→ Thin Hard Guards → Fill Plan → Browser → Save/reopen verify → Product Photos persistence**

`Send to QC` 当前始终禁止自动点击。

## 核心原则

这套系统做的事情本质上很简单：

1. 先把客户 QA、供应商资料、图片等现有资料读清楚；
2. 对照 Makro live fields，把现有资料能确定的字段先填进同一张字段表；
3. 只把仍为空的商品字段拿去联网搜索；
4. Web 搜到可靠答案就直接补回原字段，搜不到就继续空着，真实冲突就保留冲突；
5. Python 只检查页面控件和执行边界，不重新判断商品含义；
6. 人工检查只读 Fill Plan 后，才进入真实 Step 3 persistence acceptance。

不再存在独立 Final Resolve 层，也不再让 Python 用 SKU/关键词/正则去二次审核 AI 的商品语义。

## 为什么仍保留 Product Profile

Product Profile 不是审核层。它只是让原始图片和大文本 **只读一次**，压成 compact、带原始 citation 的商品事实，避免每个 Makro field 都重新发送图片。

`app/product_profile.py` 不接收 Makro target fields，只整理：identity、selected variant、supported facts、scope、真实 conflicts 和 citations。

例如 packaging/product body、cabin/rear、manual/UI language 等语义由 AI 在这里理解；Python 不维护对应商品规则。

## Stage 1 — Product Profile

默认模型：`qwen3.7-plus`。

输入：

- customer workbook / QA context；
- selected variant / SKU；
- explicit facts；
- supplier / official snapshot；
- product images。

输出：`product-profile.json`。

要求：unknown 省略；可信同属性冲突保留 candidates；negative fact 需要明确证据；citation 回到原始 source id。

Product Profile cache 不依赖 Makro schema。

## Stage 2 — Local Fill

`app/field_mapping.py` 只接收 Product Profile + 小批 live fields，不再接收原始图片或大文本。

默认：

- `--field-batch-size 12`
- `--field-concurrency 4`
- model `qwen3.7-plus`

这一步就是“现有资料先填”。AI 正常输出：

- `READY`：本地资料能确定；
- `CONFLICT`：本地资料对该字段有真实冲突；
- `MISSING`：本地资料不能确定，交给 Web Fill。

Local READY / CONFLICT 后续被冻结，Web 不再重搜或推翻。

单 batch 失败只影响对应字段；每个 batch 独立 cache。

## Stage 3 — Web Fill

只处理 Local Fill 后仍为 `MISSING / REVIEW` 的非经营字段。`REVIEW` 主要作为 packet/citation 结构校验后的保守 fallback，不是正常 Local Fill 目标状态。

默认：

- `--web-batch-size 5`
- `--web-concurrency 3`
- `--web-search-model qwen3.7-max`

Web Search 在同一次调用里完成两件事：

1. 搜索当前空字段；
2. 直接返回该字段 `READY / CONFLICT / MISSING`。

没有后续 Final Resolve。

Web citation URL 必须来自本次 Responses `web_search_call.action.sources`；模型编造 URL 不会进入最终字段表。无效/不完整 Web 输出不能覆盖 local packet。

输出仍合并到同一个 `ai-decisions.json`。

## 最终字段表

最终答案只有一个出口：`ai-decisions.json`。

它由：

- Local READY / CONFLICT；
- Web 对 unresolved 字段的有效 READY / CONFLICT 补充；
- 最终仍 MISSING / REVIEW 的字段；
- BUSINESS_LOCKED 字段；

共同组成。

不存在“Mapping 一套答案 + Final Resolve 另一套答案”的双出口。

## 模型

- Product Profile / Local Fill：`qwen3.7-plus`
- Web Fill：`qwen3.7-max`

本地 JSON task 使用 `json_object`、thinking disabled、真实 wall-clock deadline。Web Search 使用 Responses `web_search` 并校验真实 search sources。

## Cache

现在只有三层 semantic cache：

1. Product Profile cache；
2. Local field batch cache；
3. Web Fill batch cache。

相同商品热运行应尽量达到 0 model calls。Makro schema 小变化不应迫使 Product Profile 重新看图片。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等是 seller-operated data，不是商品规格。

这些字段只能来自明确 `structured / business / config / rule` 输入。供应商图片、普通 Web 搜索或 AI 推理不能编造价格、库存等业务值。

如果 required business field 缺值，Price section 仍可能无法 Save；这应报告为业务输入缺失，而不是让商品 Web 搜索去猜。

## Thin Hard Guards / Fill Plan

Python 不重新判断商品语义，只保留机械检查：

- live schema / field identity / source rebind；
- citation provenance；
- seller business lock；
- single/multi-value shape；
- 当前 Makro option 精确匹配；
- qualifier 控件是否真实存在且可匹配；
- GTIN checksum、numeric min/max、maxlength；
- Selling Price <= Base Price/MRP、MinOQ <= MaxOQ。

`AI READY + 机械约束通过 → Planner READY`。

## 浏览器执行层

`makro_preview_listing.py` 不重新解释商品。它只执行已验证 Fill Plan：

`READY → fill → React readback validation → section Save → reopen → persisted verification → collapse`

已有非-placeholder 用户值不覆盖；live schema/source/product identity drift 会 fail closed；永不自动点 `Send to QC`。

### Section Save

不会绕过 Makro 自己的 required validation。

只有当前 card 无可见 validation error、点击 Save 后确实恢复 EDIT、collapsed card 没有 Error badge，才算成功。

因此如果 Price/Product Description 仍有必填空项，正确做法是补齐上游字段或明确 business input，而不是强行绕过 Save。

### Product Photos

`--image` 是 AI evidence；只有 `--upload-image` 才是 listing 图片。

现在 `input[type=file].files > 0` **不再算上传成功**。浏览器只有观察到 Product Photos card 出现：

- 新增可见图片预览；或
- 新图片 source；或
- completion counter 增长；

才把图片记为 staged，并允许下一步 Save。

Save 后还必须验证 collapsed `Product Photos (N/5)` 计数实际增长，才算 persisted。

## 运行顺序

### 1. 扫描 live schema

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

### 2. Resolver：现有资料先填 + 空字段联网补

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

`run-manifest.json` 记录 Product Profile、Local Fill、Web Fill 的 calls / cache hits / batch count / elapsed。

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

先检查最终字段覆盖、conflicts、required blocked、business locked 和真实 DOM constraints。

### 4. 用户确认后再真实 persistence acceptance

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

## 安全不变量

复用已登录长期 Edge/CDP；多 Add Listing tabs、vertical/schema/source/identity 不一致、已有未保存 section 都 fail closed；不覆盖当前非-placeholder 用户值；option/qualifier/slot 不满足时写入前失败；React readback 不一致不算 validated；Save 后 reopen 不一致不算 persisted；Product Photos staging 不等于 persisted；`Send to QC` 始终禁止。

## 关键文件

- `app/product_profile.py`：一次 raw multimodal 商品理解 + profile cache
- `app/field_mapping.py`：Product Profile → Local Fill + field-batch cache
- `app/web_enrichment.py`：只搜 unresolved 并直接 Web Fill + cache
- `app/ai_decisions.py`：decision / provenance / schema / source validation
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：field decisions → executable Fill Plan
- `app/makro/photos.py`：Product Photos accepted-stage / persisted-count verification
- `makro_resolve_ai.py`：唯一 AI orchestration entrypoint
- `makro_plan_listing.py`：live schema / read-only Fill Plan
- `makro_preview_listing.py`：真实 browser persistence acceptance

旧 Answer Resolver、Resolution Engine、QA matcher、attribute-specific Python 商品规则、whole-product `run_ai_resolution()`、独立 Final Resolve 层都不应恢复。

## 验收

代码修改至少要求 GitHub Actions：unit tests、mock-e2e、browser dry-run、browser probe 全部通过。

真实商品下一轮先做 **Resolver + read-only Fill Plan**，不要直接写 Makro。重点检查：

- Product Profile 是否仍正确；
- Local Fill 到底先填了多少；
- Web 是否真的只搜空字段；
- Web 补回多少 READY/CONFLICT；
- 最终 required blocked 中有多少是商品资料、多少是 business input、多少是真 DOM constraint；
- packaging numeric fields 若仍被 qualifier/control metadata 阻塞，只读取真实 Makro DOM 证据再修，不猜单位。

PR 保持 Draft，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。