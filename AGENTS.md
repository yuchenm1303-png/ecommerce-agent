# AGENTS.md

## 项目目标

`ecommerce-agent` 是电商卖家后台批量信息采集、匹配、填写与校验自动化工具。
当前阶段聚焦 Makro Marketplace Seller Center：

`https://seller.makro.co.za` → `#dashboard/addListings/single`

核心流程：

读取商品资料 → 打开 Add Listing → 动态抓取页面问题 → 从明确证据解析可靠答案 →
自动填写 → 二次校验 → 人工/规则安全门 → 保存 → 记录日志

## 当前阶段（重要）

### Dynamic Field Discovery 已完成

- `makro_probe.py` 已在真实 Makro DOM 上验证：真实 label、mandatory-star、section、
  内部滚动容器、下拉 options、多值 controls 都可以动态采集。
- Semantic Field Grouping：DOM controls 按 Makro attribute 聚合成 semantic fields
  （优先稳定 id，其次 name 去索引，label 兜底）；不得硬编码任何类目的字段列表，
  多值字段必须只生成一个 semantic field。
- 已验证不同 vertical 的字段集合会变化、同一 vertical 的 DOM control 数量也可能因
  已有值/多值槽变化，但 semantic field 可以保持稳定。
- Probe 默认使用本机 Microsoft Edge（`channel="msedge"`）和独立 persistent profile
  `browser_profiles/makro-edge/`；`--keep-open` 可复用同一 Edge 会话和登录状态。
- `--scan-sections` 对所有带 EDIT 的 listing section 统一展开扫描；扫描结束只允许安全
  Cancel，禁止 Save / Send to QC，禁止上传文件。

### 当前主线：Answer Resolver + no-save real dry-run

- `app/source_bundle.py` 是商品证据统一入口：
  - 标准 CSV/XLSX/XLSM 商品表（每行一个 SKU）；
  - 客户当前 Question/Answer 工作簿；
  - 图片路径、product_url、supplemental_text 先进入 bundle，但尚未自动提取事实。
- `app/answer_resolver.py` 输入实时 `semantic_fields`，输出 evidence-grounded
  `ResolvedAnswer`。不得使用固定类目字段表。
- Resolver 状态至少保持：`resolved / needs_review / missing / conflict`。
- Resolver 只能根据 `SourceEvidence` 解析；图片识别、网页提取、知识库、LLM 都必须以后
  作为显式 evidence provider 接入，LLM 不得绕过证据层凭常识生成商品参数。
- dropdown/select 只允许规范化后的唯一精确 option match；无唯一匹配必须
  `needs_review`，不得用模糊相似度强选。
- multi-value 字段返回数组，一个 semantic field 只解析一次；执行层再映射到多个
  `_0_value/_1_value/...` controls。
- value + qualifier（如数值 + Hours/Minutes）必须分别解析，不得把单位硬塞进数字输入框。
- SKU、Listing Status、Base Price、Selling Price、MOQ、shipping 等经营字段只能来自
  明确结构化数据/config/rule，禁止 AI 或普通非结构化来源猜测。
- `app/makro_dryrun.py` 只填写 `resolved` 字段，填完立即 readback 验证。
- `makro_fill.py` 当前**只允许 no-save dry-run**：扫描全页、解析全页，但一次只填写一个
  section，停在 Save 前供人工检查。这样在禁止 Save 的阶段不会伪装成“跨 section 已完成”。
- 当前版本禁止实现真实 Save / Send to QC。未来必须使用新的显式 `--allow-save` /
  `--allow-submit` 安全门，并在真实环境验证后才允许加入。

## 架构

- `app/`：核心业务代码。
  - `data_loader.py`：CSV/XLSX 商品表读取。
  - `source_bundle.py`：`ProductSourceBundle` / `SourceEvidence`，标准商品表与 QA 文件加载。
  - `answer_resolver.py`：动态 semantic field → evidence-grounded answer；可注入
    `fallback`（仅确定性 MISSING 时咨询，经营字段永远不允许 fallback，本任务不调用 LLM）。
  - `makro_dryrun.py`：真实 Makro 安全填写与 readback。
  - `browser_session.py`：`EdgeHarness` 长期 Edge 会话抽象（localhost CDP 附加、
    不关闭外部浏览器、确定性页面选择、健康检查/重连）。
  - `makro/`：Makro 领域适配层（skill layer）。
    - `listing.py`：页面识别 / vertical 守卫 / 登录等待（只读启发式，不读凭据）。
    - `sections.py`：section 标题归一化 / 发现 / 安全 EDIT 展开 / 安全 Cancel / 逐 section 扫描。
    - `fields.py`：确定性 DOM 控件采集、滚动扫描、semantic field 分组（不硬编码类目字段）。
    - `snapshot.py`：安全 DOM 快照（清洗值/脚本/敏感属性）。
    - `locators.py`：字段定位策略（name → path → selector candidates）。
    - `fallback.py`：`SemanticFallback` 协议 + `DeterministicOnlyFallback` 占位。
    - `domain.py`：`MakroDomainAdapter` 门面，CLI 只保留策略。
  - `extractor.py`：普通 `<label>` 表单字段提取（mock/通用保守策略）。
  - `matcher.py`：旧通用字段匹配，只接受精确或明确别名。
  - `filler.py` / `validator.py`：旧通用填写与读回校验。
  - `runner.py`：旧 mock 批量执行与 JSONL 日志。
  - `platforms/`：平台适配器（`base.py`、`mock.py`、`makro.py` 委托 `app.makro`）。
- `makro_probe.py`：登录后的真实 DOM 动态探测 CLI（只读）。
- `makro_fill.py`：真实 Makro evidence-grounded no-save dry-run CLI。
- `mock_site/`：本地 mock 卖家后台。
- `tests/`：pytest 测试；`-m probe` 是需要 Chromium 的浏览器探测测试。

平台相关 DOM 规则不要塞进通用 `extractor.py`。

## 安全规则（必须遵守）

永远不要 commit / 硬编码 / 输出：

- 邮箱、密码、Cookie、Token、API Key、localStorage/sessionStorage 内容；
- `browser_profiles/`、`storage_state*.json`、`.auth/`；
- `logs/makro-probe/`、`logs/makro-fill/` 真实运行产物；
- 客户原始数据、压缩包、真实图片；
- Makro 临时 `requestId` 作为固定配置。

### 防错原则

宁可不填，也绝对不要填错。以下情况必须阻止自动保存/提交：

- 无法确认当前页面；required 字段没有答案；
- 多个明确来源冲突；
- dropdown 找不到唯一精确选项；
- 多值槽数量不足；
- qualifier 不确定；
- 填写后二次读取不一致；
- 页面异常、网络失败、session 失效。

AI 不允许凭空生成商品技术规格。经营字段必须来自明确结构化来源。

## 开发流程

每完成一块：

1. 写代码；
2. 写测试；
3. 运行 `pytest -q`；
4. 保证原有 mock 测试与 GitHub Actions（`tests` + `mock-e2e`）不被破坏；
5. 修复错误；
6. 更新 README；
7. 真实 Makro 行为只能由用户在本机已登录 Edge 中验证，不能把 fixture/mock 结果写成真实平台已验证。

## 常用命令

```powershell
python -m pytest -q
python -m pytest -q -m probe
python makro_probe.py --keep-open --scan-sections
python makro_fill.py --product private_data/product-qa.xlsx --source-format qa --dry-run
python makro_fill.py --product private_data/products.xlsx --sku ABC123 --dry-run
python main.py --dry-run
```
