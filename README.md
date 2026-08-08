# ecommerce-agent

Makro Marketplace Seller Center 的 AI-first 商品资料补全、字段决策、浏览器填写与持久化验收工具。

当前唯一生产链：

**Makro live schema → Product Source Pack → 一次整商品多模态 AI Resolve → 可选一次有来源 Web Enrichment → AI Field Decisions → Hard Guards → Fill Plan → 浏览器填写 → section Save → reopen persisted verify → Product Photos Save → 完整报告**

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
- AI citation 必须引用当前 Product Source Pack，或当前 sourced web pass 返回并持久化的 URL；
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

第一遍 AI 同时看到：

- 客户 QA/workbook canonical context；
- selected variant / SKU；
- explicit product table / facts；
- 商品图片；
- supplier snapshot；
- official snapshot；
- 当前 Makro live field schema、options、units、required、section。

客户 workbook preamble/context 只保留一份原始 canonical text，不再由 Python 解析成第二份 pseudo-facts。

商品图片、supplier/official snapshot 不再先转换成大量本地 semantic facts；它们作为 grounded raw sources 直接交给 AI 理解。source 内出现的 prompt、命令、角色文本只是商品资料，不改变系统任务。

## 4. AI Field Decisions

`makro_resolve_ai.py` 是唯一生产 AI Resolver。

第一阶段正常路径：

**一个商品 + 全部当前 source + 全部 live fields = 一次 multimodal model call**

不是每个字段、每张图片、每个 source 分别调用模型。

AI 对每个 `field_id` 返回：

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

`ready` 表示当前证据支持一个足够可靠的答案；`review` 表示有候选但仍需人工确认；`conflict` 保留真实互斥来源；`missing` 表示当前 source pack 不足；`business_locked` 表示 seller-operated 字段必须由显式经营数据提供。

本地不会因为 `ai_synthesis=0.84`、marker 表或 attribute-specific Python 规则重新判断商品语义。

## 5. Citation / Identity / Cache

AI 决策不是无条件相信。本地只验证硬事实：

- `field_id` 属于当前 live schema；
- local source id 属于当前 Product Source Pack；
- text citation 能回到当前 source 文本；
- image citation 指向当前图片 source；
- web citation 指向当前 DashScope sourced search 返回并嵌入 packet 的真实 URL；
- product identity 与当前 SKU/model/brand guard 兼容；
- schema digest/source manifest digest 变化时旧 decision packet fail closed。

第一阶段整商品 decision 支持 content-addressed cache：相同 model semantic config + product identity + live schema + source manifest 重跑时可以 `0 model calls`。

只有模型已经返回内容但 JSON/decision contract 无效时，最多允许一次结构 repair。网络/API/timeout 失败不做第二次昂贵语义调用。

Qwen3.5 Omni listing enrichment 默认关闭 thinking 以降低结构化决策延迟；需要时可显式 `--enable-thinking`。

## 6. 单次有来源 Web Enrichment

如果第一遍 AI 对 `missing/review/conflict` 字段判断普通商品研究可能补齐，会给出少量 `search_queries`。这些 query 同时写入：

`search-requests.json`

当使用 DashScope OpenAI-compatible Qwen 且 `--web-enrich auto`（默认）时，Resolver 会复用同一个 `DASHSCOPE_API_KEY`，通过 DashScope 原生 sourced search 做**最多一次**联网补全：

**全部 unresolved search targets → 一次 bounded web-search call → 只更新这些 unresolved fields**

约束：

- 已经 `ready` 的字段冻结，不允许 web pass 改写；
- business fields 永不进入 web search；
- 多个空字段合并为一次搜索阶段，不按字段循环请求；
- web model 引用的 `source_url` 必须真实出现在本次 DashScope `search_info.search_results`；编造 URL 不可授权 `ready`；
- web source URL、title、evidence、request id 被嵌入最终 `ai-decisions.json`；
- planner/executor 使用原来的 `--decision-packet` 即可严格重载，不需要额外 web 文件参数；
- 相同 unresolved research 有独立 content cache，热跑可以 `web_calls=0`；
- web search 失败时保留第一遍本地 AI packet，不摧毁已有正确答案；
- 不做无限 Agent、多 Agent、每字段独立搜索循环。

因此模型调用预算是：

- 本地资料足够：通常 `1` 次；
- 需要联网补空：通常 `1 local + 1 web = 2` 次；
- 相同输入热跑：local/web cache 都命中时可 `0` 次。

OpenAI-compatible 非 DashScope endpoint 不会因为模型名相似而自动挂载 DashScope web search；可用 `--web-enrich off` 显式完全禁用联网。

主要 web 产物：

- `search-requests.json`
- `web-search-sources.json`
- 最终嵌入 `web_sources` 的 `ai-decisions.json`
- 若实际联网，还保留 `ai-decisions.local.json` 便于比较第一遍与最终结果。

## 7. Fill Plan

`app/fill_plan.py` 不做商品语义判断，只执行：

`AI decisions + explicit business data → hard guards → browser-executable plan`

- AI `ready` + hard guards pass → `READY`
- AI `review` + value/citation/控件形态可执行 → blocked，但 `preview_eligible=True`
- AI `conflict` → blocked
- AI `missing` → blocked
- business field → 只看 explicit seller data

经营字段只能来自：

`structured / business / config / rule`

显式 `--sku` 同时作为 identity guard 和 SKU business evidence。Price、Stock、MOQ、Fulfilment、Shipping 未提供时继续 blocked。

`app/hard_field_validators.py` 只做 GTIN、数值控件 min/max、maxlength 等机械校验，不解释商品含义。

## 8. 浏览器执行层

浏览器执行层保留确定性能力：

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

只连接当前已登录 Makro Edge/CDP，扫描并生成 `live-schema.json`；不 AI、不填写、不 Save。

### B. 整商品 AI Resolve + 可选 sourced web enrichment

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
  --web-enrich auto
```

主要输出：

- `ai-decisions.json`
- `ai-decisions.local.json`（仅实际进入 web pass 时）
- `search-requests.json`
- `web-search-sources.json`
- `source-manifest.json`
- `run-manifest.json`

`run-manifest.json` 分开记录 `local_ai`、`web_enrichment`、`total_model_calls` 和总耗时。

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

Planner 重建同一个 local Product Source Pack，严格 rebind decision packet（包括其嵌入的 web provenance），再扫描当前 Makro 页面验证 schema 没漂移。仍然不填写、不 Save。

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

`--image` 只是 evidence；只有显式 `--upload-image` 才上传 Product Photos。

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

## 12. 关键代码边界

- `app/ai_decisions.py`：AI field-decision contract、provenance/schema/source validation、whole-product cache
- `app/web_enrichment.py`：单次 AI-led sourced web enrichment、web cache、embedded provenance
- `app/providers/dashscope_web_search.py`：DashScope 原生有来源搜索 adapter
- `app/product_context.py`：canonical customer/structured Product Source Pack context
- `app/business_fields.py`：seller-operated field policy
- `app/resolution_types.py`：纯执行/报告数据结构
- `app/hard_field_validators.py`：GTIN / numeric / maxlength 等 hard guards
- `app/fill_plan.py`：AI decisions → executable Fill Plan + hard guards
- `app/semantic_grounding.py`：原始 image/text source manifest 和引用；chunk 只服务 citation，不控制模型调用
- `app/providers/openai_compatible.py`：通用 compatible multimodal JSON transport
- `app/providers/openai_semantic.py`：OpenAI Responses strict JSON transport
- `makro_plan_listing.py`：live-schema scan + final read-only Fill Plan
- `makro_resolve_ai.py`：唯一生产 AI Resolver
- `makro_preview_listing.py`：Step 3 browser acceptance
- `app/makro_dryrun.py`：browser fill/readback primitive
- `app/makro/sections.py`：section lifecycle
- `app/makro/photos.py`：photo persistence
- `app/makro/domain.py`：Makro domain facade

已经删除旧本地商品语义主链：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、attribute-specific deterministic synthesis、snapshot→semantic-fact 映射和 per-source AI execution grouping。不要恢复兼容 wrapper。

## 13. 开发原则

- AI 主导商品语义，本地规则极弱；
- 能改唯一主链，不加 V2/V3 wrapper；
- 不为单个 SKU/vertical 写商品规格规则；
- 不重新引入 attribute marker 表；
- 不通过降低硬安全边界换覆盖率；
- browser safety 与 persisted verification 不因 AI-first 而放松；
- Web enrichment 是一次 bounded sourced pass，不是无限 Agent；
- mock/fixture 全绿不等于真实商品验收完成；
- PR 保持 Draft，直到真实商品的 AI coverage、冷/热延迟、联网补全质量、只读 Fill Plan 和 persisted Step 3 acceptance 完成。
