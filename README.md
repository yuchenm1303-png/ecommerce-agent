# ecommerce-agent

Makro Marketplace Seller Center 商品信息采集、证据解析、字段匹配、自动填写与持久化校验工具。

当前阶段只认一条完成链：

**客户商品资料 / QA / 图片 / supplier snapshot → Grounded Evidence → Answer Resolver → 实时 Makro semantic fields → Fill Plan → 浏览器填写 → section Save → 重新打开持久化回读 → Product Photos Save → 完整缺失/冲突/失败报告**

任何中间模块单独通过，都不等于 Step 3 完成。

## 1. 当前架构

### Source / Evidence

输入可包括：

- 客户 QA `.xlsx/.xlsm/.csv`
- QA 表头前的商品上下文：SKU、精确选定变体、supplier URL、客户备注
- 结构化商品/经营数据表
- 人工/确定性 `facts.json`
- 商品图片
- supplier / official `SourceSnapshot`
- supplemental text

QA 表头前的内容不会再被丢弃。它作为客户 source context 进入 grounded source universe；其中若包含命令、prompt、角色说明或“给某模型的指令”，模型必须把它当作**不可信证据文本**，不能执行，只能提取有来源支撑的商品事实。

### Grounded Semantic Extraction

`makro_resolve_ai.py` 把图片、snapshot 和客户上下文绑定成带 digest 的 source id。AI/provider 只能返回候选事实；候选还必须通过：

- 当前 QA 问题约束
- source id / source reference 校验
- evidence text 校验
- 当前商品身份校验
- business-field lock
- 冲突检测
- confidence gate
- Makro 字段约束

历史 `validated-semantic-evidence.json` 在用于真实 Fill Plan 前，会重新绑定到**本次实际传入的图片 / snapshot / 客户上下文**。底层文件变化后，旧 packet 不能静默复用。

### Answer Resolver / Fill Plan

Resolver 保持四种核心状态：

- `resolved`
- `needs_review`
- `conflict`
- `missing`

`eligible_for_autofill` 与 `preview_eligible` 是两个不同安全级别：

- `eligible_for_autofill`：满足正式自动填写门槛
- `preview_eligible`：只允许在显式人工验收 draft 中查看/持久化，不等于生产自动化安全

同一条泛化 `ai_synthesis` 证据 + 同一答案不能同时授权多个不同字段。例如一个泛化 `120°` 不能同时填 `Interior Field of View` 和 `Exterior Field of View`。

### QA → Makro 匹配

只允许：

- exact normalized match
- 人工审核的 explicit label alias
- 人工审核的 explicit section override

不使用 fuzzy similarity 强行匹配。

配置格式：

```json
{
  "schema_version": 1,
  "vertical": "vehicle_camera_system",
  "aliases": {},
  "sections": {
    "Height": "Additional Description"
  }
}
```

`vehicle_camera_system` 当前配置：

`config/makro_aliases/vehicle_camera_system.json`

该配置解决客户 QA 缺少真实 section metadata 时的 `Height` 跨 section 歧义；Python 代码不写死 SKU 或技术规格。

## 2. 经营字段硬规则

以下 seller-controlled 数据只能来自客户明确的 `structured / business / config / rule` 来源：

- SKU
- Listing Status
- Base Price / Selling Price
- Stock
- MOQ
- Fulfilment
- Shipping SLA
- Selling Region

AI、图片、supplier 营销页不能猜这些值。

显式命令行 `--sku` 本身就是 seller-controlled 输入，因此会同时作为商品身份守卫和 `SKU` business evidence；价格、库存等未提供字段仍然保持 blocked。

## 3. 浏览器执行层

真实 Makro 使用长期 Edge/CDP，会话与 source capture 浏览器隔离。

已有能力：

- 动态发现 Step 3 semantic fields
- text / textarea
- native/custom dropdown
- number
- value + qualifier
- multi-value `+` 动态新增槽位
- pre-save immediate + React-settled readback
- exact section Save
- Save 后重新打开 persisted readback
- Product Photos file-input staging
- Product Photos Save 后完成计数验证

正式 multi-value 执行不会只填前几个值。如果页面槽位不够，会先在**该字段自己的 wrapper** 内点击 `+`、重新扫描、直到槽位足够；如果仍不足，则在任何部分值写入前失败。

## 4. Section lifecycle 只有一套实现

`app/makro/sections.py` 是 Step 3 section 生命周期的唯一实现：

- find
- EDIT
- validation error detection
- Cancel
- Save
- Save 后 card collapse / error badge 检查

synthetic/test-only 代码不得维护另一套 Save/Cancel 逻辑。

`app/makro_dryrun.py`：

- `fill_resolved_field()` = pre-save 写入 + React settled readback
- `verify_resolved_field()` = Save 后重新打开的只读 persisted verification

**pre-save `validated` 不等于 persisted。**

## 5. Product Photos

Evidence 图片和 listing 图片严格分开：

- `--image`：只进入 evidence/grounding
- `--upload-image`：用户明确授权上传到 Makro Product Photos

图片状态也严格分开：

- `staged`：文件已进入当前 Product Photos 编辑事务
- `persisted_verified`：该卡片 Save 后 `(x/5)` 完成计数按预期增加，并能重新打开检查

不能再因为页面出现预览就宣称图片已保存，也不能在 Save 前等待 `(x/5)` 增长。

## 6. 两种真实运行模式

### 单 section 诊断

```powershell
python makro_preview_listing.py `
  --qa <qa.xlsx> `
  --expected-vertical <vertical> `
  --section "Product Description" `
  [evidence options]
```

特点：

- 只填一个 section
- 不点 section Save
- 页面保持打开供人工检查
- 永不 Send to QC

### 完整 Step 3 持久化验收

```powershell
python makro_preview_listing.py `
  --qa <qa.xlsx> `
  --expected-vertical <vertical> `
  --alias-config <matching-config.json> `
  --all-step3 `
  --allow-section-save `
  [evidence options] `
  [--upload-image <listing-image>]
```

`--all-step3` 没有 `--allow-section-save` 会直接拒绝执行。不存在“全量填完再 Cancel 丢值”的模式。

每个字段 section 按以下事务执行：

1. 从同一个 live Fill Plan 选择候选
2. 写入
3. pre-save readback
4. 截图
5. 检查 Makro validation
6. 点击该 section 自己的 Save
7. 等待 card 折叠回 EDIT
8. 重新打开
9. 对本轮写入值做 persisted readback
10. 截图
11. Cancel 这次**只读重开事务**使卡片折叠；已保存值不丢失

某个 section 失败会记录错误和截图，并尽量清理该未保存事务后继续其他 section，以便一次验收收集完整问题集合。

Product Photos 最后执行：

1. staging 明确的 `--upload-image`
2. screenshot
3. Product Photos Save
4. 轮询 `(x/5)` 持久化计数
5. 重新打开并截图
6. 折叠只读重开事务

永不点击 `Send to QC`。

## 7. 完成状态

完整验收报告必须区分：

- `draft_persisted_complete`
  - Makro 草稿层的 required field cards / optional card / Product Photos 是否真实 Save 并持久化复核通过
- `autofill_safe_complete`
  - 在 draft persisted 基础上，`required_blocked == 0`
  - 并且没有 review-only 候选被当成正式自动化答案

草稿能 Save 不等于生产自动化已经安全。

`report.json` 同时保存：

- 完整 `Fill Plan`
- blocked gate reason 汇总
- 每个字段的 answer/source/confidence/provenance
- pre-save execution result
- section Save result
- persisted verification result
- Product Photos staging/persistence result
- screenshots
- `send_to_qc_clicked=false`

## 8. 推荐当前工作流

### A. 重新生成 grounded semantic evidence

```powershell
python makro_resolve_ai.py `
  --provider openai-compatible `
  --base-url <provider-base-url> `
  --model <multimodal-model> `
  --api-key-env <KEY_ENV> `
  --qa <qa.xlsx> `
  --image <evidence-image-1> `
  --image <evidence-image-2> `
  --supplier-snapshot <source-snapshot.json>
```

输出包括：

- `validated-semantic-evidence.json`
- `source-manifest.json`
- `semantic-batches.json`
- `resolution.json/.xlsx`
- `review-queue.json/.xlsx`
- `run-manifest.json`

### B. 只读 live Fill Plan

```powershell
python makro_plan_listing.py `
  --qa <qa.xlsx> `
  --sku <sku> `
  --expected-vertical <vertical> `
  --evidence-packet <validated-semantic-evidence.json> `
  --supplier-snapshot <source-snapshot.json> `
  --image <same-evidence-image-1> `
  --image <same-evidence-image-2> `
  --alias-config <matching-config.json>
```

Grounded packet 会在这里重新绑定到这些当前 source files。

### C. 完整 Step 3 acceptance

在检查 Fill Plan 后，由用户在已登录的长期 Makro Edge 上运行 `--all-step3 --allow-section-save`。

## 9. 安全不变量

- 多个 Add Listing tab → fail closed
- vertical 不一致 → fail closed
- 已有未保存 section → full acceptance 停止，不擅自 Cancel
- source identity / digest 不一致 → fail closed
- 真实 conflict → 不静默覆盖
- dropdown 无唯一精确 option → 不填
- multi-value 槽位不足 → 不做部分写入
- qualifier 不确定/控件缺失 → 不写
- business field 无客户明确数据 → 不猜
- React settled readback 不一致 → 不算 validated
- Save 后 persisted readback 不一致 → 不算 persisted
- Product Photos staged 不等于 persisted
- `Send to QC` 当前始终禁止自动点击

## 10. 开发原则

- 能改主链，不加 wrapper
- 能复用 domain primitive，不复制第二套实现
- 没有真实问题证明需要，不增加 abstraction
- 不为某个 SKU / vertical 硬编码产品规格
- synthetic coverage 只用于执行层新回归，不再作为 Step 3 完成标准
- GitHub CI / mock 全绿只代表代码回归通过；真实 Makro 完成必须由真实商品验收报告证明
