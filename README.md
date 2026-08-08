# ecommerce-agent

Makro Marketplace Seller Center 商品信息采集、证据解析、字段匹配、自动填写与持久化校验工具。

当前阶段只认一条完成链：

**实时 Makro live schema → 客户商品资料 / QA / 图片 / supplier snapshot → Grounded Evidence → Answer Resolver → Fill Plan → 浏览器填写 → section Save → 重新打开持久化回读 → Product Photos Save → 完整缺失/冲突/失败报告**

任何中间模块单独通过，都不等于 Step 3 完成。`Send to QC` 仍是后续独立高风险提交动作，当前 runner 永远不点击。

## 1. Source / Evidence

输入可包括：客户 QA、QA 表头前商品上下文、结构化商品/经营表、`facts.json`、商品图片、supplier/official snapshot、supplemental text。

QA 表头前的 SKU、精确选定变体、supplier URL、客户备注不会再被丢弃。customer preamble 只有一个 canonical source；Resolver 抽取和后续 evidence rebind 使用完全相同的文本，不需要重复传 `--supplemental-text` 来凑 hash。

source 内出现的 prompt、命令、角色说明只是不可信证据文本，模型不得执行，只能提取有来源支持的商品事实。

`makro_resolve_ai.py` 将图片、snapshot、客户上下文绑定为带 digest 的 source id。AI/provider 只生成候选事实；候选仍需通过 QA 范围、source/evidence、商品身份、business lock、冲突、confidence 和字段约束。

历史 semantic packet 若引用 `image:/supplier:/official:/customer-text:` source id，在真实 Fill Plan 前必须重新绑定本次实际 source universe；图片/snapshot/客户上下文变化后旧 packet 会 fail closed。

## 2. Resolver 执行模型

生产 Resolver 是 **source-first**，不再使用 `question batch × source chunk`：

- 一张商品图片 = 一个 logical source，正常路径只识别一次；
- 一个 supplier/official snapshot 的多个文本 chunk = 一个 logical source，一次模型请求；chunk 只用于精确 citation；
- customer context = 一个 logical source；
- 每个 logical source 都面对完整 pending non-business question set，保证不同 source 仍可形成真实 conflict；
- 默认 `--source-concurrency 2`，独立 logical source 有界并发；只改变首轮延迟，不改变最终 evidence 合并顺序；
- `--fail-on-source-error` 自动退回串行，保持真正 fail-fast；
- 一条坏 fact 只丢该 fact，不重复识别整张图；仅当该 source 的所有候选都被拒绝时允许最多一次 semantic repair；
- API/网络失败不做 semantic repair；SDK 隐式 retry 关闭；
- 单 source 请求有明确 timeout；
- 严格验证通过的 source 立即写 content-addressed cache，中断后重跑只补未完成 source；
- cache key 绑定商品 identity、模型/语义配置、完整问题 schema、source digest 和 grounding contract；纯 transport timeout 变化不让语义缓存失效；
- `semantic-sources.json` 记录每个 source 的 START/DONE/FAILED、耗时、calls、cache、facts/rejected 和总 elapsed。

## 3. Resolver / Fill Plan

核心状态：`resolved / needs_review / conflict / missing`。

- `eligible_for_autofill`：达到正式自动填写门槛。
- `preview_eligible`：只允许显式进入人工验收 draft，不等于生产自动化安全。

真实来源冲突不静默覆盖。例如 720p vs 1080p、3.0 vs 3.16 inch 仍保持 conflict。

Resolver 只消除能够确定性证明的“伪冲突/串字段”：

- 泛化“多镜头”与唯一精确数量 `2` 兼容时采用精确数量；
- set-like 字段只在某个**已有来源值**是所有其他来源的明确 superset 时采用该来源，不自行拼接 union；
- `TF Card expandable` 不是 `Storage Capacity`；
- packaging dimensions 不是产品本体 Width/Depth/Height；
- 商品 Brand 不是 Vehicle Brand；
- manual language 不是设备 UI/System Languages Supported；
- reverse-assist/reversing-image 功能本身不证明包含 rear/reverse camera；
- internal memory = none 不证明 SD/memory card 未随箱附送。

同一条泛化 `ai_synthesis` 的同一 source_reference + 同一答案不能同时授权多个不同字段；模型即使为两个字段写出不同 evidence prose 也不能绕过该限制，例如一个泛化 `120°` 不能同时填 Interior/Exterior FOV。

QA → live field 只允许 exact-normalized、人工审核 alias、人工审核 section override；不使用 fuzzy 强行匹配。

`config/makro_aliases/vehicle_camera_system.json` 当前通过 section override 解决 Q59 `Height` 跨 section 歧义，不在 Python 中硬编码 SKU/规格。

## 4. 经营字段

SKU、Listing Status、Base/Selling Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 只能来自客户明确的 `structured/business/config/rule`。

显式 `--sku` 同时是 identity guard 和 `SKU` business evidence。价格、库存等未提供值继续 blocked，AI/图片/supplier 页面不得猜。

## 5. 浏览器执行

长期 Edge/CDP 运行时动态发现 Step 3 semantic fields，不写死类目字段总数。

已接入：

- text / textarea
- native/custom dropdown
- number
- value + qualifier
- multi-value `+` 动态扩槽
- pre-save immediate + React-settled readback
- section Save
- Save 后重新打开 persisted readback
- Product Photos staging + Save + completion-count persistence check

multi-value 如果答案值数量超过当前槽位，executor 只在该字段自己的 wrapper 内点击 `+` 并重新扫描；仍不足则在任何部分答案写入前失败。qualifier control 缺失也在写主值前失败。

## 6. Section lifecycle

`app/makro/sections.py` 是唯一 section lifecycle 实现：find、EDIT、validation errors、Cancel、Save、Save 后折叠/error badge 检查。

`app/makro_dryrun.py`：

- `fill_resolved_field()` = pre-save write + React settled readback
- `verify_resolved_field()` = Save 后重新打开 persisted verification

pre-save `validated` 不等于 persisted。

## 7. Product Photos

- `--image`：evidence/grounding only
- `--upload-image`：明确上传到 Makro 的 listing image

状态：

- `staged`：文件进入 Product Photos 编辑事务
- `persisted_verified`：卡片 Save 后 `(x/5)` 计数增加，并可重新打开检查

页面出现图片预览不再被误报成“已经保存”；反过来也不会在 Save 前等待 `(x/5)` 增长。

## 8. 正确运行顺序

A. 先从当前 Makro 页面只读导出 live schema：

```powershell
python makro_plan_listing.py `
  --qa <qa.xlsx> `
  --sku <sku> `
  --expected-vertical <vertical> `
  --alias-config <matching-config.json> `
  --output-dir <scan-output>
```

这一步只读 Makro，得到 `live-schema.json`。AI Resolver 必须使用同一份 schema，避免客户 QA 没覆盖的实时字段被漏掉。

B. Grounded AI evidence：

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url <base> `
  --model <vision-model> `
  --api-key-env <KEY_ENV> `
  --qa <qa.xlsx> `
  --live-schema <live-schema.json> `
  --image <img1> `
  --image <img2> `
  --supplier-snapshot <snapshot.json> `
  --source-concurrency 2
```

C. 使用同一 live schema 做最终只读 Fill Plan：

```powershell
python makro_plan_listing.py `
  --qa <qa.xlsx> `
  --sku <sku> `
  --expected-vertical <vertical> `
  --live-schema <same-live-schema.json> `
  --evidence-packet <validated-semantic-evidence.json> `
  --supplier-snapshot <snapshot.json> `
  --image <same-img1> `
  --image <same-img2> `
  --alias-config <matching-config.json>
```

D. 人工检查 Fill Plan 后，再运行完整 Step 3 persisted acceptance：

```powershell
python makro_preview_listing.py `
  --qa <qa.xlsx> `
  --expected-vertical <vertical> `
  --alias-config <matching-config.json> `
  --all-step3 `
  --allow-section-save `
  [same evidence/live-schema options] `
  [--upload-image <listing-image>]
```

`--all-step3` 没有 `--allow-section-save` 会拒绝执行；不再存在“全量填写后 Cancel 丢值”的模式。

每个字段 card：Fill Plan → 写入 → pre-save readback → screenshot → validation → Save → collapse → reopen → persisted readback → screenshot → 折叠只读重开事务。

某个 card 失败会记录错误/截图并尽量清理未保存事务后继续，目的是一次暴露整页问题而不是逐个磨。

Product Photos：stage → screenshot → Save → poll `(x/5)` → reopen → screenshot → collapse。

始终 `Send to QC=False`。

## 9. 完成状态

报告必须分别给出：

- `draft_persisted_complete`：Makro 草稿卡片和图片是否真正 Save + reopen 验证通过。
- `autofill_safe_complete`：draft persisted 基础上 `required_blocked == 0` 且没有 review-only 候选被当正式自动化答案。

`report.json` 保存完整 Fill Plan、blocked reasons、字段 source/confidence/provenance、pre-save 结果、Save 结果、persisted result、photo result、screenshots。

## 10. 安全不变量

- 多 listing tabs → fail closed
- vertical 不一致 → fail closed
- 已有未保存 section → full acceptance 停止
- source digest/identity 不一致 → fail closed
- conflict → 不覆盖
- dropdown 无唯一精确 option → 不填
- multi-value/qualifier shape 不足 → 不做部分写入
- business field 无客户明确数据 → 不猜
- React settled readback 不一致 → 不算 validated
- Save 后 readback 不一致 → 不算 persisted
- photo staged 不等于 persisted
- `Send to QC` 当前始终禁止

## 11. 开发原则

能改主链不加 wrapper；能复用 domain primitive 不复制第二套实现；没有真实问题证明需要不增加 abstraction；不为单个 SKU/vertical 硬编码技术规格或答案；synthetic coverage 只用于执行层回归，不再作为 Step 3 完成标准。
