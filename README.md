# ecommerce-agent

Makro Marketplace Seller Center 的商品资料采集、AI 字段补全、只读规划与浏览器持久化验收工具。

当前唯一生产链：

**Makro live schema → 一个 1688/供应商商品链接 → 自动采集完整页面证据 → AI 直接填写 Makro fields → Web 只补仍为空的字段 → Thin Hard Guards → Fill Plan → Browser → Save/reopen verify → Product Photos persistence**

`Send to QC` 始终禁止自动点击。

## 核心原则

1. 用户启动一个新商品时只提供商品链接；不再要求人工 SKU、旧 QA 答案、旧商品快照或半成品资料。
2. 程序机械采集当前页面：rendered text、参数行、JSON-LD、页面内嵌 SKU/规格数据和 full-page screenshot。
3. Python 不解释这些资料。AI 直接对照 Makro live fields 输出 `READY / CONFLICT / MISSING`。
4. Local `READY / CONFLICT` 冻结；只有仍为空的非经营字段进入 Web。
5. Web 以 exact product URL + 已知 Local fields 为商品身份锚点；搜到就补，搜不到留空，真实冲突保留。
6. 不存在 Product Profile、Final Resolve 或 Python 商品语义复核。
7. SKU ID 是 seller-controlled identifier，不是商品属性。Makro SKU 由商品 URL 稳定机械生成，不参与 AI 商品理解。

## Stage 0 — 商品页自动采集

`makro_resolve_ai.py --product-url <URL>` 使用独立 source Edge（默认 CDP `9333`），与 Makro seller Edge 分离。

采集内容包括页面标题/meta、rendered text、table/dl 参数行、JSON-LD、页面 DOM / inline script 中与 SKU/规格/variant/offer 相关的有限原始片段，以及 full-page screenshot。

采集器只记录原始页面证据，不点击 SKU、不选择款式、不判断哪个参数属于哪个字段，也不绕过 CAPTCHA/风控。若页面需要合法人工验证，完成后用 `--source-use-current-page` 继续。

## Stage 1 — Local Fill

默认模型：`qwen3.7-plus`。

`app/field_mapping.py` 直接接收原始页面 evidence + 小批 Makro live fields。AI 自己负责跨语言理解、规格关系、scope、冲突和字段映射。

输出只有：

- `READY`：当前证据明确支持；
- `CONFLICT`：同一字段存在真实冲突；
- `MISSING`：当前资料不能确定。

机械分批只按 live schema 顺序切片；Python 不建立 camera/storage/dimension 等商品规则表。

## Stage 2 — Web Fill

默认模型：`qwen3.7-max`。

只处理 Local Fill 后仍为 `MISSING / REVIEW` 的非经营字段。Web 调用一次完成“搜索 + 回答字段”，没有后续 Final Resolve。

Web citation URL 必须来自本次真实 `web_search` sources；模型编造 URL 不会进入最终字段表。

## Makro SKU

`app/business_fields.py` 根据 exact product URL 生成稳定的 12 位数字 SKU。相同商品 URL 的 query/tracking 参数变化不会改变 SKU。

它只是机械 seller identifier：不传给 AI 作为商品身份、不拿去搜互联网、不用它否定供应商页面。Planner/Executor 把它作为 `rule` 类型 business input 使用。

价格、库存、MOQ、Fulfilment、Shipping SLA、Listing Status 等其他经营字段仍必须来自明确 seller data，缺失就保持 blocked，不能让 AI/Web 猜。

## Thin Hard Guards

Python 只保留机械执行边界：live schema/source rebind、citation provenance、business lock、single/multi-value shape、Makro option/qualifier、GTIN/numeric/maxlength、价格/MOQ关系、DOM唯一定位、React readback、Save/reopen persistence、Product Photos persistence 和禁止 Send to QC。

Python 不判断 cabin/rear、manual/UI language、包装/机身尺寸、网页是不是同款等商品语义。

## 运行顺序

先扫描 Makro schema：

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

然后只给商品链接运行 Resolver：

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 `
  --model qwen3.7-plus `
  --web-search-model qwen3.7-max `
  --api-key-env DASHSCOPE_API_KEY `
  --live-schema <live-schema.json> `
  --product-url <1688-or-supplier-product-url> `
  --disable-thinking `
  --web-enrich auto
```

Resolver 主要输出：

- `primary-source/source-snapshot.json`
- `primary-source/source-page.png`
- `ai-decisions.local.json`
- `search-requests.json`
- `web-evidence.json`
- `web-search-sources.json`
- `ai-decisions.json`
- `source-manifest.json`
- `run-manifest.json`

最终只读 Fill Plan 用同一 URL、同一 snapshot 和 screenshot strict rebind：

```powershell
python makro_plan_listing.py `
  --decision-packet <ai-decisions.json> `
  --live-schema <live-schema.json> `
  --product-url <same-product-url> `
  --supplier-snapshot <primary-source/source-snapshot.json> `
  --image <primary-source/source-page.png> `
  --expected-vertical vehicle_camera_system
```

只有 read-only acceptance 通过后，才使用生产执行入口：

```powershell
python makro_execute_listing.py `
  --decision-packet <ai-decisions.json> `
  --live-schema <live-schema.json> `
  --product-url <same-product-url> `
  --supplier-snapshot <primary-source/source-snapshot.json> `
  --image <primary-source/source-page.png> `
  --expected-vertical vehicle_camera_system `
  --all-step3 `
  --allow-section-save `
  [--upload-image <listing-image>]
```

`makro_execute_listing.py` 不读旧 QA、不接受人工 `--sku`，只执行已经验证过的 plan。它复用成熟的字段写入/Save/reopen/Product Photos 浏览器函数，但不会重新判断商品语义。

## 关键文件

- `app/source_capture.py`：supplier page 自动机械采集
- `app/source_snapshot.py`：原始页面 snapshot + bounded variant data
- `app/semantic_grounding.py`：evidence / citation manifest
- `app/field_mapping.py`：原始商品 evidence → Local Fill
- `app/web_enrichment.py`：unresolved-only Web Fill
- `app/business_fields.py`：seller business policy + generated SKU
- `app/ai_decisions.py`：decision / provenance / schema validation
- `app/fill_plan.py`：decisions → executable plan
- `makro_resolve_ai.py`：单商品链接 AI Resolver
- `makro_plan_listing.py`：live schema / read-only Fill Plan
- `makro_execute_listing.py`：单商品链接生产 browser persistence runner
- `makro_preview_listing.py`：底层浏览器执行 helpers / 旧兼容入口，不再作为新生产入口

## 验收

每次正式修改至少要求 GitHub Actions：unit tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品先做：

**product URL capture → cold Resolver → hot Resolver → read-only Fill Plan**

不要直接写 Makro。先检查页面证据是否抓完整、Local/Web/final 字段结果、冲突、required blocked 和生成的 seller SKU，再决定是否进入 persistence。
