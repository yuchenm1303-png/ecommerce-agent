# ecommerce-agent

Makro Marketplace Seller Center 商品信息采集、证据解析、字段匹配、自动填写与持久化校验工具。

当前只认一条完成链：

**Makro 实时只读 schema → 客户商品资料 / QA / 图片 / supplier snapshot → Grounded Evidence → Answer Resolver → Fill Plan → 浏览器填写 → section Save → 重新打开持久化回读 → Product Photos Save → 完整缺失/冲突/失败报告**

任何中间模块单独通过，都不等于 Step 3 完成。`Send to QC` 仍是后续独立高风险提交动作，当前 runner 永远不点击。

## 1. 先拿当前 Makro live schema

客户 Excel 不是 Makro 全部字段的完整 schema。真实页面可能存在客户 QA 没列出的非经营字段，例如包装 Length/Breadth/Height/Weight。

所以真实商品必须先用 `makro_plan_listing.py` 对当前已登录 Makro Step 3 做**只读扫描**并生成 `live-schema.json`。这一步不会填写、Save、上传图片或 Send to QC。

后续 Resolver、最终 Fill Plan 和真实 acceptance 必须使用**同一份** live schema。写页面前会再次检查当前页面字段合同；schema 漂移则 fail closed。

## 2. Source / Evidence

输入可包括：客户 QA、QA 表头前商品上下文、结构化商品/经营表、`facts.json`、商品图片、supplier/official snapshot、supplemental text。

QA 表头前的 SKU、精确选定变体、supplier URL、客户备注不会被丢弃。source 内出现的 prompt、命令、角色说明只是不可信证据文本，模型不得执行，只能提取有来源支持的商品事实。

图片、snapshot、客户上下文都绑定 content digest。历史 semantic packet 在进入真实 Fill Plan 前必须重新绑定本次 source universe；图片/snapshot/客户上下文变化后旧 packet fail closed。

## 3. AI Resolver：source-first，而不是 batch × source

生产 Resolver 只有 `makro_resolve_ai.py` 一条入口。

旧的 `question batch × source chunk` 路径已经删除。当前执行模型：

- 一张图片 = 一个 logical source，正常情况下只调用 AI 一次；
- 一份 supplier/official snapshot = 一个 logical source；内部 text chunks 只用于精确 citation，不增加调用次数；
- 一份 customer context = 一个 logical source；
- 每个 logical source 一次性面对完整 pending 非经营问题集；
- 不同 source 分别产生 facts，最后由本地 Resolver 判断 conflict / needs_review / missing。

因此同一张商品长图不会因为 70+ 问题被拆成多批而重复识别。

### 逐 fact fail-closed

一个模型 fact 验证失败，不再导致整张图片/整个 source 重跑：

- 合法 sibling facts 保留；
- 坏 fact 丢弃并记录 warning；
- 只有一个 source 的所有候选都被严格验证拒绝时，才允许最多 1 次显式 repair；
- identity 冲突仍是硬失败。

这没有降低 trust boundary。每条保留下来的 fact 仍需通过 QA key、source id、逐字文本 evidence、图片可见依据、direct/synthesis、business lock、identity 等检查。

### 内容哈希缓存

严格验证通过的 per-source packet 会缓存到 `logs/semantic-cache/`。缓存键绑定：

- provider / model / 实际 provider config；
- product identity；
- 当前完整问题 schema；
- source id / source type / source digest。

相同运行被中断后重跑，已经完成的图片/source 可直接 cache hit，不再重新识图。缓存命中后仍会针对当前 schema/source/identity 重新验证。

### 可观察、可控的耗时

终端逐 source 输出：

`START → CACHE HIT（如有）→ DONE/FAILED → facts → rejected → model_calls → elapsed`

`semantic-sources.json` 同时保存每个 source 的调用次数、缓存命中、耗时、失败原因。

生产创建的 SDK client 关闭隐式 transport retry；单 source 默认请求超时为 120 秒。网络/API 失败不会把已完成 source 推倒重来。

## 4. Resolver / Fill Plan

核心状态：`resolved / needs_review / conflict / missing`。

- `eligible_for_autofill`：达到正式自动填写门槛；
- `preview_eligible`：只允许显式进入人工验收 draft，不等于生产自动化安全。

真实来源冲突不静默覆盖。同一条泛化 `ai_synthesis` 证据 + 同一答案不能同时授权多个不同字段，例如一个泛化 `120°` 不能同时填 Interior/Exterior FOV。

QA → live field 只允许 exact-normalized、人工审核 alias、人工审核 section override；不使用 fuzzy 强行匹配。

`config/makro_aliases/vehicle_camera_system.json` 当前通过 section override 解决 Q59 `Height` 跨 section 歧义，不在 Python 中硬编码 SKU/规格。

## 5. 经营字段

SKU、Listing Status、Base/Selling Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 只能来自客户明确的 `structured/business/config/rule`。

显式 `--sku` 同时是 identity guard 和 SKU business evidence。价格、库存等未提供值继续 blocked，AI/图片/supplier 页面不得猜。

## 6. 浏览器执行

长期 Edge/CDP 运行时动态发现 Step 3 semantic fields，不写死类目字段总数。

已接入：text/textarea、native/custom dropdown、number、value+qualifier、multi-value `+` 动态扩槽、React settled readback、section Save、Save 后 persisted readback、Product Photos staging+Save+计数验证。

multi-value 如果答案值数量超过当前槽位，executor 只在该字段自己的 wrapper 内点击 `+` 并重新扫描；仍不足则在任何部分答案写入前失败。qualifier control 缺失也在写主值前失败。

## 7. Section lifecycle

`app/makro/sections.py` 是唯一 section lifecycle 实现：find、EDIT、validation errors、Cancel、Save、Save 后折叠/error badge 检查。

`app/makro_dryrun.py`：

- `fill_resolved_field()` = pre-save write + React-settled readback；
- `verify_resolved_field()` = Save 后重新打开 persisted verification。

pre-save `validated` 不等于 persisted。

## 8. Product Photos

- `--image`：evidence/grounding only；
- `--upload-image`：明确上传到 Makro 的 listing image。

状态：

- `staged`：文件进入 Product Photos 编辑事务；
- `persisted_verified`：卡片 Save 后 `(x/5)` 计数增加，并可重新打开检查。

页面出现图片预览不再被误报成“已经保存”；Save 前也不会错误等待 `(x/5)` 增长。

## 9. 当前推荐真实链

### A. Read-only live schema

```powershell
python makro_plan_listing.py `
  --qa <qa.xlsx> `
  --sku <sku> `
  --expected-vertical <vertical> `
  --supplier-snapshot <snapshot.json> `
  --image <img1> `
  --image <img2> `
  --alias-config <matching-config.json>
```

记录输出的 `live-schema.json`。

### B. Source-first grounded AI evidence

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url <base> `
  --model <vision-model> `
  --api-key-env <KEY_ENV> `
  --qa <qa.xlsx> `
  --live-schema <live-schema.json> `
  --sku <sku> `
  --image <img1> `
  --image <img2> `
  --supplier-snapshot <snapshot.json>
```

主要输出：

- `validated-semantic-evidence.json`
- `semantic-sources.json`
- `resolution.json/.xlsx`
- `review-queue.json/.xlsx`
- `run-manifest.json`

### C. Read-only final Fill Plan

再次运行 `makro_plan_listing.py`，带：

- 同一 `live-schema.json`；
- 新 `validated-semantic-evidence.json`；
- 同一客户 QA / SKU / supplier snapshot / evidence images / alias config。

先人工检查 READY、review、conflict、missing、business_locked 和 required_blocked。

### D. 完整 Step 3 persistence acceptance

```powershell
python makro_preview_listing.py `
  --qa <qa.xlsx> `
  --live-schema <same-live-schema.json> `
  --expected-vertical <vertical> `
  --alias-config <matching-config.json> `
  --all-step3 `
  --allow-section-save `
  [evidence options] `
  [--upload-image <listing-image>]
```

`--all-step3` 没有 `--allow-section-save` 会拒绝执行；不再存在“全量填写后 Cancel 丢值”的模式。

每个字段 card：Fill Plan → 写入 → pre-save readback → screenshot → validation → Save → collapse → reopen → persisted readback → screenshot → 折叠只读重开事务。

某个 card 失败会记录错误/截图并尽量清理未保存事务后继续，目的是一次暴露整页问题而不是逐个磨。

Product Photos：stage → screenshot → Save → poll `(x/5)` → reopen → screenshot → collapse。

始终 `Send to QC=False`。

## 10. 完成状态

报告必须分别给出：

- `draft_persisted_complete`：Makro 草稿卡片和图片是否真正 Save + reopen 验证通过；
- `autofill_safe_complete`：draft persisted 基础上 `required_blocked == 0` 且没有 review-only 候选被当正式自动化答案。

`report.json` 保存完整 Fill Plan、blocked reasons、字段 source/confidence/provenance、pre-save 结果、Save 结果、persisted result、photo result、screenshots。

## 11. 安全不变量

- 多 listing tabs → fail closed；
- vertical 不一致 → fail closed；
- 已有未保存 section → full acceptance 停止；
- live schema 漂移 → 写页面前 fail closed；
- source digest/identity 不一致 → fail closed；
- conflict → 不覆盖；
- dropdown 无唯一精确 option → 不填；
- multi-value/qualifier shape 不足 → 不做部分写入；
- business field 无客户明确数据 → 不猜；
- React settled readback 不一致 → 不算 validated；
- Save 后 readback 不一致 → 不算 persisted；
- photo staged 不等于 persisted；
- `Send to QC` 当前始终禁止。

## 12. 开发原则

能改主链不加 wrapper；能复用 domain primitive 不复制第二套实现；没有真实问题证明需要不增加 abstraction；不为单个 SKU/vertical 硬编码产品规格；synthetic coverage 只用于执行层回归，不再作为 Step 3 完成标准。

AI Resolver 同样只有一条主路径：`app/semantic_grounding.py → app/semantic_sources.py → makro_resolve_ai.py`。旧 `semantic_batching.py` 与旧 `makro_resolve_openai.py` 已删除，不保留兼容层。
