# ecommerce-agent

Makro Marketplace Seller Center 的 AI-first 商品资料补全、字段决策、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → Product Source Pack → 一次整商品多模态 AI Resolve → AI Field Decisions → Hard Guards → Fill Plan → 浏览器填写 → section Save → reopen persisted verify → Product Photos Save → 完整报告**

`Send to QC` 仍是独立高风险提交动作，当前 runner 永远不点击。

## 1. 设计原则

### AI 负责商品语义

自然语言理解、跨语言映射、同义词、简单计数、规格含义、来源综合、冲突判断、字段与商品描述的语义对应，都交给 AI。

本地 Python **不再**维护这类商品规则：

- `黑色 -> Black`
- `双镜头 -> 2`
- `G-Sensor -> Yes`
- `cabin -> In-Car`
- FOV / Vehicle Brand / SD Card / Camera Type 等字段专属 marker
- QA alias / section override / fuzzy field mapping

换类目时不应该继续增加 attribute-specific `if/else`。

### Python 只守硬边界

本地代码保留真正确定、可机械验证的规则：

- seller-operated business fields 禁止 AI 猜；
- 商品 identity、live schema、source manifest 必须匹配；
- AI citation 必须引用当前 source pack；
- dropdown/listbox 只能使用当前 Makro 的有效 option；
- qualifier/unit 必须真实存在；
- multi-value 必须能由当前控件完整表达；
- GTIN/EAN checksum；
- numeric min/max、maxlength；
- Selling Price <= Base Price/MRP；
- MinOQ <= MaxOQ；
- 浏览器字段定位、React settled readback、Save/reopen/persisted verification；
- `Send to QC` 禁止自动点击。

## 2. 唯一目标 Schema：Makro live schema

Makro 当前页面真实发现出的字段就是 AI 的唯一目标字段集合。

客户 QA 不再定义“系统要回答哪些字段”，也不再通过 QA matcher / alias config 去映射 live fields。客户工作簿现在主要是高价值商品信息来源：

- 已确认 Answer；
- SKU；
- selected variant；
- supplier URL；
- 客户备注；
- 其他结构化商品/经营数据。

每个 live field 由结构化 schema 生成稳定 `field_id`，AI 直接针对该 field 决策。

## 3. Product Source Pack

AI 第一遍可以同时看到：

- 客户 QA/workbook context；
- selected variant / SKU；
- explicit product table / facts；
- 商品图片；
- supplier snapshot；
- official snapshot；
- 当前 Makro live field schema、options、units、required、section。

source 内出现的 prompt、命令、角色文本只是商品资料，不应被当作运行指令。

商品图片、supplier/official snapshot 不再先转换成大量本地 `semantic facts`；它们作为 grounded raw sources 直接交给 AI 理解。

## 4. AI Field Decisions

`makro_resolve_ai.py` 是唯一生产 AI Resolver。

正常路径不是“每个 source 调一次模型”，而是：

**一个商品 + 全部当前 source + 全部 live fields = 一次 multimodal model call**

AI 对每个 `field_id` 直接返回：

- `ready`
- `review`
- `conflict`
- `missing`
- `business_locked`

并附带：

- `values`
- `qualifier`
- `confidence`
- `citations`
- `alternatives`
- `reason`
- 可选 `search_queries`

### 状态含义

`ready`：当前资料支持一个足够可靠的答案，可以进入硬约束检查。

`review`：有可用候选，但 AI 判断证据、scope、identity 或解释仍需要人工确认。

`conflict`：可信来源对同一目标字段给出实质不兼容的答案；AI必须保留 alternatives 和 citations，不能静默选一个。

`missing`：当前 source pack 不足以回答。AI可以建议少量针对性的网页搜索 query。

`business_locked`：价格、库存、MOQ、fulfilment、shipping、listing status 等 seller-operated 字段；AI无权生成。

## 5. Citation / Identity / Cache

AI 决策不是无条件相信。

本地只验证硬事实：

- `field_id` 必须属于当前 live schema；
- source id 必须属于当前 Product Source Pack；
- text citation 必须能回到当前 source 文本；
- image citation 必须引用当前图片 source；
- product identity 必须与当前 SKU/model/brand guard 兼容；
- schema digest/source manifest digest 变化时旧 decision packet fail closed。

整商品 decision 支持 content-addressed cache：相同 model semantic config + product identity + live schema + source manifest 重跑时可以 `0 model calls`。

Qwen3.5 Omni listing enrichment 默认关闭 thinking 以降低结构化抽取延迟；需要时可显式 `--enable-thinking`。

## 6. Web Enrichment 状态

第一遍 AI 会为值得进一步研究的 `missing/review` 字段输出 `search_queries`，保存为：

`search-requests.json`

**当前版本还没有把自动互联网搜索/抓取接入生产主链。**

计划中的下一阶段是 bounded enrichment：

1. 只针对 unresolved fields 合并成少量搜索任务；
2. 并行搜索少量高相关页面；
3. 用商品 identity/variant 过滤错误页面；
4. 抓取最相关网页；
5. 第二次 text-only AI 只重新决策 unresolved fields；
6. 不做无限 Agent 循环。

因此当前 `missing` 表示“现有 Product Source Pack 没找到”，不等同于“互联网上不存在答案”。

## 7. Fill Plan

`app/fill_plan.py` 不再做商品语义判断。

它只把 AI Field Decisions 变成页面执行计划，并应用硬约束：

- AI `ready` + hard guards pass → `READY`
- AI `review` + value/citation/控件形态可执行 → blocked，但 `preview_eligible=True`
- AI `conflict` → blocked
- AI `missing` → blocked
- business field → 只看 explicit seller data

经营字段只能来自：

`structured / business / config / rule`

显式 `--sku` 同时作为 identity guard 和 SKU business evidence。Price、Stock、MOQ、Fulfilment、Shipping 未提供时继续 blocked。

## 8. 浏览器执行层

浏览器执行层保留前面已经验证的确定性能力：

- 实时字段发现；
- text / textarea；
- native/custom dropdown；
- number；
- value + qualifier；
- multi-value `+` 扩槽；
- React settled readback；
- section Save；
- Save 后 reopen persisted verification；
- Product Photos staging + Save + persisted count/reopen verification。

AI 不直接操作 DOM，也不决定 Save/QC。

## 9. 正确运行顺序

### A. 首次只读扫描 live schema

```powershell
python makro_plan_listing.py `
  --scan-live-schema `
  --expected-vertical vehicle_camera_system
```

这一步只连接当前已登录 Makro Edge/CDP，扫描页面并生成：

`live-schema.json`

不需要 AI，不填写，不 Save。

### B. 一次整商品 AI Resolve

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url <base-url> `
  --model <multimodal-model> `
  --api-key-env <KEY_ENV> `
  --qa <qa.xlsx> `
  --live-schema <live-schema.json> `
  --sku <sku> `
  --image <img1> `
  --image <img2> `
  --supplier-snapshot <snapshot.json> `
  --disable-thinking
```

主要输出：

- `ai-decisions.json`
- `search-requests.json`
- `source-manifest.json`
- `run-manifest.json`

正常路径目标：`model_calls=1`。

### C. 最终只读 Fill Plan

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

Planner 会重新构造同一个 Product Source Pack，严格 rebind decision packet，并重新扫描当前 Makro 页面验证 schema 没漂移。

仍然不填写、不 Save。

### D. 人工检查后执行真实 Step 3 persistence acceptance

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

`--image` 只是 AI evidence；只有显式 `--upload-image` 才上传 Product Photos。

## 10. Step 3 安全不变量

- CDP/长期 Edge 登录态复用；
- 多个 Add Listing tab → fail closed；
- vertical 不一致 → fail closed；
- decision/source/schema digest 不一致 → fail closed；
- 当前 live schema 与 AI 使用 schema 不一致 → fail closed；
- 已有未保存 section → full acceptance 停止；
- 当前控件已有非 placeholder 值 → 不覆盖；
- option/qualifier 不存在 → 写入前失败；
- multi-value 槽位不足 → 不做部分写入；
- React settled readback 不一致 → 不算 validated；
- Save 后 reopen readback 不一致 → 不算 persisted；
- Product Photos staged 不等于 persisted；
- `Send to QC` 始终禁止。

## 11. 完成状态

完整报告区分：

- `draft_persisted_complete`：草稿卡片和图片是否真正 Save + reopen 验证通过；
- `autofill_safe_complete`：在 draft persisted 基础上 required blocked=0，且没有 review-only 候选被当正式自动化答案。

不得把 saved draft 等同于 production-safe autofill。

## 12. 代码边界

当前关键文件：

- `app/ai_decisions.py`：AI field-decision contract、citation/schema/source validation、whole-product cache
- `app/product_context.py`：canonical customer/structured Product Source Pack context
- `app/business_fields.py`：seller-operated field policy
- `app/resolution_types.py`：纯执行/报告数据结构
- `app/fact_validators.py`：GTIN / numeric / maxlength 等 hard guards
- `app/fill_plan.py`：AI decisions → executable Fill Plan + hard guards
- `app/semantic_grounding.py`：原始 image/text source manifest 和引用
- `app/providers/*`：通用 multimodal JSON task transport
- `makro_plan_listing.py`：live-schema scan + final read-only Fill Plan
- `makro_resolve_ai.py`：唯一生产 AI Resolver
- `makro_preview_listing.py`：Step 3 browser acceptance
- `app/makro_dryrun.py`：browser fill/readback primitive
- `app/makro/sections.py`：section lifecycle
- `app/makro/photos.py`：photo persistence
- `app/makro/domain.py`：Makro domain facade

已经删除旧的本地商品语义主链：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、attribute-specific deterministic synthesis 和 snapshot→semantic-fact 映射。不要恢复兼容 wrapper。

## 13. 开发原则

- AI 主导商品语义，本地规则极弱；
- 能改唯一主链，不加 V2/V3 wrapper；
- 不为单个 SKU/vertical 写商品规格规则；
- 不重新引入 attribute marker 表；
- 不通过降低安全边界换覆盖率；
- browser safety 与 persisted verification 不因 AI-first 而放松；
- mock/fixture 全绿不等于真实商品验收完成；
- PR 保持 Draft，直到真实商品的 AI coverage、首轮延迟、只读 Fill Plan 和 persisted Step 3 acceptance 完成。
