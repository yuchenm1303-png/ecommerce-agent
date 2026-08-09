# ecommerce-agent

Makro Marketplace Seller Center 的商品资料采集、AI 字段补全、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → 1688/供应商商品链接 + 指定 SKU → 独立 source Edge 自动采集当前商品页 → AI 直接填写 Makro fields → Web 只补仍为空的字段 → Thin Hard Guards → Fill Plan → Browser → Save/reopen verify → Product Photos persistence**

`Send to QC` 当前始终禁止自动点击。

## 核心原则

这套系统现在只做一条直线：

1. 从确定的商品链接自动收集当前商品页的文本、参数表、详情内容和整页图片；
2. 连同客户已有 QA/资料和指定 SKU，一起直接交给 AI 填 Makro live fields；
3. 已经确定的 READY / CONFLICT 冻结；
4. 只有仍为空的商品字段才联网搜索；
5. Web 搜到可靠答案就直接补回原字段，搜不到继续空着，真实冲突保留冲突；
6. Python 只负责采集、调度、来源、页面控件和执行边界，不重新判断商品含义；
7. 人工检查只读 Fill Plan 后，才进入真实 Step 3 persistence acceptance。

生产 Resolver 中不再有 Product Profile，也没有 Final Resolve，更没有 Python 商品语义复核。

## Stage 0 — 自动采集确定商品页

`makro_resolve_ai.py --product-url <1688/供应商链接>` 会调用 `app/source_capture.py`。

使用独立 source Edge：

- 默认 profile：`browser_profiles/source-edge`
- 默认 CDP：`9333`
- 与 Makro seller Edge 完全分离
- 自动滚动页面，触发 lazy-loaded 内容
- 保存 rendered text、table/dl rows、JSON-LD、meta
- 保存 full-page screenshot
- 不解释商品语义
- 不绕过 CAPTCHA / 风控

如果供应商页面要求合法登录或人工验证，程序停在 source Edge，用户完成后使用 `--source-use-current-page` 继续采集。

客户以前需要手工截图“商品属性 / 包装信息 / 详情页”的工作，现在优先由这一步自动完成。

## Stage 1 — Local Fill：AI 直接填字段

`app/field_mapping.py` 直接接收原始商品证据 + 一小批当前 Makro live fields。

默认：

- model：`qwen3.7-plus`
- `--field-batch-size 12`
- `--field-concurrency 4`

AI 对每个字段直接输出：

- `READY`：当前资料明确支持；
- `CONFLICT`：当前资料对同一字段真实冲突；
- `MISSING`：当前资料不能确定。

没有“先整理 Product Profile，再让第二个 AI 重新解释”的中间跳转。

原始 supplier snapshot、页面 screenshot、客户 QA、额外图片/structured facts 都可以直接成为 AI evidence。

Local READY / CONFLICT 后续被冻结，Web 不重新搜索、不推翻。

机械 batch 只按 live schema 顺序切片，不建立 camera/storage/dimension 等 Python 商品分类表。

## Stage 2 — Web Fill：只补空字段

只处理 Local Fill 后仍为 `MISSING / REVIEW` 的非经营字段。

默认：

- model：`qwen3.7-max`
- `--web-batch-size 5`
- `--web-concurrency 3`

Web AI 同时获得：

- `source_product_url`：当前确定的 1688/供应商商品链接；
- 已经确定的 Local READY / CONFLICT：作为当前商品和 selected variant 的指纹；
- 仅本批仍为空的 Makro fields。

Web 优先以 exact product URL 为身份锚点。其他网页仅仅型号同名不够，必须与当前商品的具体已知特征一致。

一次 Web 调用直接完成“搜索 + 回答字段”，没有后续 Final Resolve。

Web citation URL 必须来自本次 Responses `web_search_call.action.sources`；模型编造 URL 不会进入最终字段表。无效 Web 输出不会覆盖原本的 unresolved 字段。

## 最终字段表

最终只有一个出口：`ai-decisions.json`。

它包含：

- Local READY / CONFLICT；
- Web 对 unresolved 字段的 READY / CONFLICT 补充；
- 最终仍 MISSING / REVIEW 的字段；
- BUSINESS_LOCKED 字段。

没有“第一套答案 + 第二套复核答案”的双出口。

## 模型职责

- Local Fill：`qwen3.7-plus`
- Web Fill：`qwen3.7-max`

本地 JSON task 使用 `json_object`、thinking disabled、真实 wall-clock deadline。Web 使用 Responses `web_search` 并校验真实 search sources。

## Cache

生产 Resolver 现在只有两类 semantic cache：

1. Local field batch cache；
2. Web Fill batch cache。

相同商品证据 + 相同 live schema 热运行应尽量达到 0 model calls。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等是 seller-operated data，不是商品规格。

这些字段只能来自明确 `structured / business / config / rule` 输入。供应商页面、图片、普通 Web 或 AI 推理不能编造价格、库存等经营值。

如果 required business field 缺值，应报告为业务输入缺失，而不是让商品搜索去猜。

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

Python 不判断网页是不是同款，不判断 cabin/rear、manual/UI language、包装/机身尺寸等商品含义。

## 浏览器执行层

`makro_preview_listing.py` 不重新解释商品，只执行已验证 Fill Plan：

`READY → fill → React readback validation → section Save → reopen → persisted verification → collapse`

已有非-placeholder 用户值不覆盖；live schema/source/product identity drift fail closed；永不自动点 `Send to QC`。

### Section Save

不会绕过 Makro 自己的 required validation。

只有当前 card 无可见 validation error、点击 Save 后确实恢复 EDIT、collapsed card 没有 Error badge，才算成功。

### Product Photos

`--image` 是 AI evidence；只有 `--upload-image` 才是 listing 图片。

`input[type=file].files > 0` 不算上传成功。只有 Makro card 出现新增可见图片预览、新图片 source 或 completion counter 增长，才把图片记为 staged。Save 后还必须验证 collapsed `Product Photos (N/5)` 计数实际增长。

## 运行顺序

### 1. 扫描 live schema

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

### 2. Resolver：从商品链接自动采集并直接填字段

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
  --product-url <1688-or-supplier-product-url> `
  --disable-thinking `
  --web-enrich auto `
  --request-timeout-seconds 120
```

如果已经有额外客户资料，仍可附加：

```powershell
  --image <extra-image> `
  --supplier-snapshot <extra-snapshot.json> `
  --facts-json <confirmed-facts.json>
```

主要输出：

- `primary-source/source-snapshot.json`（使用 `--product-url` 时）
- `primary-source/source-page.png`（使用 `--product-url` 时）
- `ai-decisions.local.json`
- `search-requests.json`
- `web-evidence.json`
- `web-search-sources.json`
- `ai-decisions.json`
- `source-manifest.json`
- `run-manifest.json`

`run-manifest.json` 记录 source capture、Local Fill、Web Fill 的 calls / cache hits / batch count / elapsed。

### 3. 最终只读 Fill Plan

使用 Resolver 输出的同一套 source evidence / `ai-decisions.json` 生成只读 Fill Plan。先检查字段覆盖、conflicts、required blocked、business locked 和真实 DOM constraints。

### 4. 用户确认后再真实 persistence acceptance

真实执行继续使用 `makro_preview_listing.py`，只执行已验证 Fill Plan。不会自动 `Send to QC`。

## 关键文件

- `app/source_capture.py`：exact supplier page 自动机械采集
- `app/source_snapshot.py`：supplier page snapshot
- `app/semantic_grounding.py`：原始 evidence / citation manifest
- `app/field_mapping.py`：原始商品 evidence → Local Fill
- `app/web_enrichment.py`：只搜 unresolved → Web Fill
- `app/ai_decisions.py`：decision / provenance / schema validation
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：field decisions → executable Fill Plan
- `app/makro/photos.py`：Product Photos accepted-stage / persisted-count verification
- `makro_resolve_ai.py`：唯一 AI Resolver orchestration entrypoint
- `makro_capture_source.py`：独立 supplier capture CLI
- `makro_plan_listing.py`：live schema / read-only Fill Plan
- `makro_preview_listing.py`：真实 browser persistence acceptance

`app/product_profile.py` 暂时可保留作历史兼容/旧测试代码，但已经不属于生产 Resolver 路径，不应重新接回 `makro_resolve_ai.py`。

## 验收

代码修改至少要求 GitHub Actions：unit tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品下一轮先做：

**product URL capture → cold Resolver → hot Resolver → read-only Fill Plan**

不要直接写 Makro。重点检查：

- exact 商品页实际抓到了多少属性/包装/详情内容；
- Local Fill READY / CONFLICT / MISSING；
- Web 是否真的只收到 unresolved；
- Web 是否使用 exact product URL + 当前已知字段作为身份锚点；
- 最终 required blocked 中多少是商品资料、真实 conflict、business input、DOM constraint；
- packaging numeric fields 若仍被 qualifier/control metadata 阻塞，只读真实 Makro DOM 后再处理，不猜单位。

PR 保持 Draft，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。
