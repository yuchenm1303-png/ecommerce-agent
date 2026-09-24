# ecommerce-agent

Makro Marketplace Seller Center 的商品资料采集、AI 字段补全、只读规划与浏览器持久化验收工具。

当前唯一生产链：

**Makro live schema → 一个 1688/供应商商品链接 → 自动采集原始页面证据 → Local AI 直接填字段 → Web 只补 MISSING → Thin Hard Guards → Fill Plan → Browser → Save/reopen verify → Product Photos persistence**

`Send to QC` 始终禁止自动点击。

## 核心原则

1. 新商品的人工产品输入只有商品链接；不要求人工 SKU、旧 QA 答案、旧 snapshot 或半成品资料。
2. Python 机械采集页面，不解释商品：结构化参数、rendered text、variant/SKU 原始数据、整页截图和页面暴露的大图。
3. Local AI 直接读取这些原始证据并输出 `READY / CONFLICT / MISSING`。
4. `READY / CONFLICT` 冻结；只有 `MISSING` 进入 Web。
5. Web 同时获得 exact URL、原始供应商页面证据和 Local 已知字段，避免只凭 `M8` 这类通用型号串商品。
6. Python citation guard 只验证引用的 source address 是否真实存在，不逐字匹配 evidence_text，也不在 AI 后面重新判断商品语义。
7. 不存在 Product Profile、Final Resolve、Python 商品语义复核或循环复核。
8. Makro SKU 是 seller-controlled identifier，根据商品 URL 稳定生成，不参与 AI 商品理解。

## Stage 0 — Source Capture

`makro_resolve_ai.py --product-url <URL>` 使用独立 source Edge（默认 CDP `9333`），与 Makro seller Edge 分离。

机械采集：

- title / meta；
- table / dl / 明确 key-value 参数行；
- rendered visible text；
- JSON-LD；
- 页面 DOM / inline script 中 bounded SKU、variant、spec、offer、length/width/height/weight 原始片段；
- full-page screenshot；
- 页面已经暴露的大尺寸商品/详情图片 URL，并自动下载可用图片作为独立视觉证据。

采集器不点击 SKU、不选择款式、不解释字段、不绕过 CAPTCHA/风控。

### Hot source cache

Resolver 默认把同一商品 URL 的原始 snapshot / screenshot / product-image bytes 短期缓存 15 分钟。紧接着的 hot rerun 复用完全相同的 source bytes，使 Local/Web semantic cache 可以真正命中。

需要重新读取最新网页时显式使用：

`--refresh-source`

source cache 只是网页字节复用，不是商品事实层。

## Stage 1 — Local Fill

默认模型：`qwen3.7-plus`。

`app/field_mapping.py` 将原始页面 evidence + 当前小批 Makro live fields 直接交给 AI。AI负责跨语言理解、variant、scope、dimension axes、视觉/文字冲突和字段映射。

输出只有：

- `READY`：证据明确支持；
- `CONFLICT`：同一字段存在真实冲突；
- `MISSING`：不能确定。

结构化来源中的 `length / width / height` 标签保持原样给 AI；Makro field 的附近 `context_text` 也会保留，用于识别页面固定单位。Python 不交换尺寸轴、不补单位、不判断语义。

## Stage 2 — Web Fill

默认模型：`qwen3.7-max`。

Web 只接收 Local 的 `MISSING` 字段。每个 Web batch 同时看到：

- exact `source_product_url`；
- 原始 primary supplier evidence；
- Local `READY / CONFLICT` 商品指纹；
- 当前待补字段。

Web prompt 明确规定：只有同一个通用型号名不构成同款证据；其他 URL 必须与当前原始商品证据中的多个具体锚点一致，否则返回 `MISSING`。Python 不替 AI 做“是不是同款”的语义判断，只检查引用 URL 是否确实来自本次 Web search sources。

没有后续 Final Resolve。

## Makro SKU / Business fields

`app/business_fields.py` 根据 exact product URL 生成稳定 12 位数字 SKU；query/tracking 参数变化不改变结果。

SKU 不进入商品搜索或 AI 身份判断。

价格、库存、MOQ、Fulfilment、Shipping SLA、Listing Status 等其他经营字段只能来自明确 seller data；缺失保持 blocked，AI/Web 不猜。

## Thin Hard Guards

Python 仅保留机械执行边界：

- live schema / source strict rebind；
- citation source address provenance；
- seller business lock；
- single/multi-value shape；
- Makro option / qualifier 控件；
- GTIN、numeric min/max、maxlength；
- Selling Price <= Base Price/MRP、MinOQ <= MaxOQ；
- DOM 唯一定位、React readback；
- Save/reopen persistence；
- Product Photos persistence；
- 禁止 Send to QC。

Python 不判断 cabin/rear、manual/UI language、包装/机身尺寸、商品同款、功能缺失等商品语义。

## 只读验收顺序

每轮真实商品验收先重新扫描当前 Makro live schema：

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

然后只给商品链接运行 cold Resolver：

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

立即用完全相同命令运行 hot Resolver。正常情况下 `source_capture.source_cache_hit=true`，成功缓存的 Local/Web batches 不应再次调用模型。

主要输出：

- `primary-source/source-snapshot.json`
- `primary-source/source-page.png`
- `primary-source/product-images/*`
- `ai-decisions.local.json`
- `search-requests.json`
- `web-evidence.json`
- `web-search-sources.json`
- `ai-decisions.json`
- `source-manifest.json`
- `run-manifest.json`

最终只读 Fill Plan 必须使用 Resolver 的**同一套 source bytes** strict rebind：同一 snapshot、full-page screenshot，以及 `run-manifest.json` 中列出的每一张 `primary_source_product_images`（通过重复 `--image` 传入）。

只有 read-only acceptance 通过后才进入 `makro_execute_listing.py`。真实执行同样必须 strict rebind 完整 source set；永不自动 `Send to QC`。

## 关键文件

- `app/source_capture.py`：supplier page 采集、页面大图下载、短期 source byte cache
- `app/source_snapshot.py`：原始页面结构化 snapshot
- `app/semantic_grounding.py`：保持结构的原始 evidence / citation manifest
- `app/field_mapping.py`：原始 evidence → Local Fill
- `app/web_enrichment.py`：primary evidence 绑定的 MISSING-only Web Fill
- `app/ai_decisions.py`：结构/来源地址校验，不做商品语义复核
- `app/business_fields.py`：seller business policy + generated SKU
- `app/fill_plan.py`：decisions → executable plan
- `makro_resolve_ai.py`：单商品链接 Resolver
- `makro_plan_listing.py`：live schema / read-only Fill Plan
- `makro_execute_listing.py`：生产 browser persistence runner

## 验收门槛

代码修改至少要求 GitHub Actions 的 unit tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品固定顺序：

**fresh live schema → cold Resolver → hot Resolver → read-only Fill Plan**

在 read-only 结果通过前，不真实写 Makro。