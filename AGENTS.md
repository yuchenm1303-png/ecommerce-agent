# AGENTS.md

## 当前唯一目标

`ecommerce-agent` 当前聚焦 Makro Marketplace Seller Center 的真实商品 Step 3：

`客户商品资料 / 客户 QA / 图片 / supplier snapshot`
→ `grounded evidence`
→ `Answer Resolver`
→ `实时 Makro semantic fields`
→ `Fill Plan`
→ `真实填写`
→ `section Save`
→ `重新打开并持久化回读`
→ `Product Photos Save + 持久化验证`
→ `完整缺失/冲突/失败报告`

任何一段没接通，都不能把 Step 3 宣称为“完成”。单个模块通过、DOM 中临时出现值、或者 mock/coverage 全绿，都不等于完整商品链路完成。

`Send to QC` 是最终高风险提交动作，当前仍然绝对禁止自动点击。

## 已完成的基础能力

- Makro Dynamic Field Discovery 已在真实页面验证：label、mandatory、section、内部滚动、下拉 options、单位、multi-value/`+` 控件均可动态发现。
- 70+ 普通 Step 3 控件的浏览器执行能力已经验证；不要重复做 synthetic coverage，除非出现新的执行层回归。
- `app/makro_dryrun.py` 的 `fill_resolved_field()` 负责 pre-save 写入 + React settled readback。
- `verify_resolved_field()` 负责 Save 后重新打开的只读持久化验证。不要把 pre-save `validated` 当作 persisted。
- `app/makro/photos.py` 负责 Product Photos：文件 staging 与 Save 后计数验证分开；staged 不等于 persisted。
- `app/makro/sections.py` 是 section 生命周期的唯一实现：发现、EDIT、Cancel、Save、validation error 检测。不要在其他模块复制 Save/Cancel 实现。

## Answer Resolver / Evidence 规则

- QA 问题来自实时客户工作簿；不得写死类目字段列表。
- QA 表头前的客户商品上下文也是 source：SKU、精确选定变体、supplier URL、客户说明不能静默丢弃。
- 图片 / supplier / official / AI 结果必须带 `source_reference`、`evidence_text`、confidence/provenance。
- Resolver 至少保持：`resolved / needs_review / missing / conflict`。
- 多个明确来源冲突时保持 conflict，禁止按优先级偷偷覆盖。
- `preview_eligible` 只表示“可以显式进入人工 review draft”，绝不等于 `eligible_for_autofill`。
- 同一条 `ai_synthesis` 证据 + 同一答案如果被绑定到多个不同字段，字段归属不唯一，必须禁止 preview/persist（例如一个泛化 120° 不能同时填 Interior/Exterior FOV）。
- dropdown/select 只接受唯一精确 option match；禁止模糊强选。
- value + qualifier 分开解析和填写。
- EAN/GTIN、数值范围、maxlength、价格关系、MOQ 关系等必须经过确定性 validator。

## 经营字段硬规则

SKU、Listing Status、Base/Selling Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等 seller-controlled 数据只能来自客户明确的 structured/business/config/rule 来源。

禁止 AI、图片、supplier 营销页或“看起来合理”的默认值生成经营数据。

## Step 3 两种真实运行模式

### 1. 单 section 诊断

`makro_preview_listing.py --section ...`

- 可以填写 READY；显式开启时可填写 `preview_eligible`。
- 不点 section Save。
- 留在页面供人工检查。
- 不点 Send to QC。

### 2. 完整 Step 3 持久化验收

`makro_preview_listing.py --all-step3 --allow-section-save ...`

`--all-step3` 没有 `--allow-section-save` 必须拒绝执行；不再存在“全量填完然后 Cancel 丢值”的假全量模式。

每个字段 section 必须：

1. 用 Fill Plan 选择允许执行的候选；
2. 写入并 pre-save readback；
3. 截图；
4. 检查 Makro validation；
5. 点击该 section 自己的 Save；
6. 等待 card 折叠回 EDIT；
7. 重新打开；
8. 对本次写入值做只读 persisted readback；
9. 再折叠；
10. 失败要记录原因/截图并继续收集其他 section 的问题。

Product Photos 必须：

1. 只上传显式 `--upload-image`；`--image` 只是 evidence，绝不隐式上传；
2. staging 后不宣称成功持久化；
3. 点击 Product Photos 自己的 Save；
4. 验证 `(x/5)` 计数增长；
5. 重新打开并确认保存后的图片状态；
6. 永不点击 Send to QC。

## 完成状态必须拆开报告

- `draft_persisted_complete`：Makro 草稿层是否真正保存并重开验证通过。
- `autofill_safe_complete`：在 draft 持久化通过基础上，required blocked=0，且没有 review-only 候选被当成正式自动化答案。

不得把前者写成后者。`report.json` 必须保留完整 Fill Plan、blocked gate reason、每个 section 的执行/Save/持久化结果和图片结果。

## 浏览器安全

- 真实 Makro 使用长期 Edge/CDP；复用用户现有登录态。
- 多个 Add Listing tab 时 fail closed。
- vertical 不匹配时 fail closed。
- 已有未保存 section 时完整验收必须停止，不能擅自 Cancel 用户内容。
- 不关闭、不重启长期 Edge；不要修改 browser profile。
- `requestId` 永远不能硬编码为配置。

## 代码收敛原则

这是强约束：

- 能改现有主函数，不加 wrapper。
- 能复用 domain primitive，不复制第二套实现。
- 没有真实问题证明需要，不增加 abstraction。
- synthetic/test-only 代码不得拥有与 production 不同的 Save/Cancel 实现。
- 新代码必须优先减少状态歧义和重复路径；测试全绿不代表架构合理。
- 不为某个 SKU / vertical 硬编码技术规格或答案。

当前主链关键文件：

- `app/qa_catalog.py`：QA + 表头前客户上下文
- `app/resolver_inputs.py`：统一 evidence 输入
- `makro_resolve_ai.py`：grounded multimodal extraction（不打开 Makro）
- `app/resolution_engine.py` / `app/fill_plan.py`：答案、安全门、完整 live-field 计划
- `app/makro/fields.py`：实时字段发现
- `app/makro/sections.py`：唯一 section 生命周期/Save/Cancel
- `app/makro_dryrun.py`：pre-save fill + post-save verify
- `app/makro/photos.py`：图片 staging/persistence
- `app/makro/domain.py`：Makro domain facade
- `makro_preview_listing.py`：单 section 诊断 + 完整 Step 3 acceptance 编排

旧 mock/coverage/visual-hold 模块只用于回归或历史兼容，不得作为当前真实商品完成标准。

## Secrets / 客户数据

永远不要 commit、硬编码或输出：密码、Cookie、Token、API Key、localStorage/sessionStorage、browser profile、真实客户原始文件/图片、临时 Makro requestId。

## 开发验收

每次正式修改后至少：

1. `pytest -q`
2. GitHub Actions `tests` 通过
3. `mock-e2e` 通过
4. 真实 Makro 行为必须由用户在本机已登录 Edge 中验证
5. 真实验收前先生成/检查 Resolver + Fill Plan；不把 fixture/mock 结果写成真实平台已验证
6. PR 保持 draft/unmerged，直到真实 M8 Step 3 acceptance 完成并审查结果
