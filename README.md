# ecommerce-agent

电商卖家后台批量信息采集、匹配、填写与校验自动化原型。

项目已经从本地 `mock_site` 进入真实平台适配阶段：**Makro Marketplace Seller Center**。

核心目标：

**读取商品资料 → 打开 Add Listing → 动态抓取页面问题 → 从证据中解析可靠答案 → 自动填写 → 二次校验 → 人工/规则安全门 → 保存 → 记录日志**

## 当前进度

### V0.1：本地闭环

- 支持读取 `.csv`、`.xlsx`、`.xlsm` 商品表格。
- 按 SKU 批量处理多个商品。
- 使用 Playwright 控制 Chromium 浏览器。
- 从普通 HTML `<label>` 与输入控件关系中抓取页面问题。
- 对字段进行保守匹配：只接受精确匹配或明确配置的同义字段。
- 支持文本框、下拉框、复选框的基础填写。
- 每个字段填写后重新读取页面值进行校验。
- 必填字段找不到可靠答案、商品身份不一致或校验失败时阻止保存。
- 支持 `--dry-run`。
- 每个商品的执行结果写入 `logs/*.jsonl`。
- GitHub Actions 执行单元测试和 mock 浏览器 E2E。

### V0.2：Makro 动态页面探测

- 新增 `app/platforms/makro.py`，校验 Makro Add a Single Listing hash route。
- 不依赖 `requestId` 作为长期稳定标识，因为该参数由平台动态生成。
- 新增 `makro_probe.py`：在用户自己的电脑上登录后，采集真实页面控件的 DOM 元数据。
- 使用本地持久化 Playwright Edge profile，账号密码不写入代码、不上传 GitHub。
- 可展开所有带 EDIT 的 section，识别真实 label、mandatory-star、下拉选项、内部滚动容器。
- Semantic Field Grouping 把多个 DOM control 聚合成真实 Makro attribute，多值字段不会被误算成多道问题。
- 已用真实产物验证不同 vertical 的动态字段变化：页面字段数量和控件数量变化时，semantic field 仍可稳定还原。
- Probe 不记录 Cookie/token/sessionStorage/Authorization，不点击 `Save` / `Send to QC`。

### V0.3：证据驱动 Answer Resolver + 真实 Dry-Run Fill

- 新增 `app/source_bundle.py`：统一商品证据模型 `ProductSourceBundle` / `SourceEvidence`。
- 支持两种明确资料：标准“每行一个商品”的 CSV/XLSX/XLSM，以及客户当前使用的 Question/Answer 工作簿。
- 新增 `app/answer_resolver.py`：输入当前页面动态 `semantic_fields`，按 `attribute_key + label` 从明确证据解析答案，不依赖固定类目字段表。
- 来源冲突返回 `conflict`；没有证据返回 `missing`；下拉选项无法唯一精确匹配返回 `needs_review`。
- SKU、Listing Status、价格、MOQ、shipping 等经营字段只接受明确结构化数据/config/rule，不允许 AI 或非结构化来源猜测。
- 支持 multi-value 数组，以及 value + qualifier（例如数值 + Hours/Minutes）解析。
- 新增 `app/makro_dryrun.py`：只填写 `resolved` 字段，填后立即 readback 验证。
- 新增 `makro_fill.py`：真实 Makro dry-run CLI，动态扫描 → 解析 → 填写 → 回读，**绝不点击 Save / Send to QC**。
- 当前 no-save 阶段一次只填写一个 section，并停在该 section 的 Save 前供人工检查；其他 section 会完成解析但不写入，避免跨 section 时依赖保存未验证的数据。

## 项目结构

```text
ecommerce-agent/
├─ app/
│  ├─ data_loader.py
│  ├─ source_bundle.py       # 商品证据统一模型 / table + QA 文件加载
│  ├─ answer_resolver.py     # 动态 semantic field → 证据答案
│  ├─ makro_dryrun.py        # 真实 Makro 安全填写 + readback
│  ├─ extractor.py
│  ├─ matcher.py
│  ├─ filler.py
│  ├─ validator.py
│  ├─ logger.py
│  ├─ runner.py
│  └─ platforms/
│     ├─ base.py
│     ├─ mock.py
│     └─ makro.py
├─ data/
│  └─ products.csv
├─ mock_site/
│  └─ index.html
├─ tests/
├─ logs/
├─ makro_probe.py            # 登录后动态采集真实 Makro DOM
├─ makro_fill.py             # 证据驱动真实 dry-run fill
├─ main.py
└─ requirements.txt
```

## Windows 本地安装

建议 Python 3.11+。

```powershell
git clone https://github.com/yuchenm1303-png/ecommerce-agent.git
cd ecommerce-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

默认真实 Makro 使用本机 Microsoft Edge `channel="msedge"`，Chromium 主要用于测试。

## 本地 mock

终端 1：

```powershell
python -m http.server 8000 --directory mock_site
```

终端 2：

```powershell
python main.py --dry-run
```

## Makro 动态页面 Probe

不要把 Makro 邮箱、密码写进代码或发到仓库。

```powershell
python makro_probe.py --keep-open --scan-sections
```

流程：

1. 程序复用 `browser_profiles/makro-edge/`；登录仍有效则直接使用，失效时手动登录一次；
2. 从 Makro UI 正常进入 `Add a Single Listing`；
3. 回终端按 Enter；
4. 程序展开并扫描当前页面所有 listing section；
5. `--keep-open` 可在同一个 Edge 会话里继续扫描下一商品/类目。

### Probe 采集能力

- 识别 `input` / `textarea` / `select` / combobox / dropdown / checkbox / radio / autocomplete；
- 遍历页面与内部滚动容器，等待懒加载；
- 从 Makro `.styles__AttributeItemLabelName...` 提取真实 label；
- 从 `sup.mandatory-star__MandatoryStarContainer` 判断 required；
- 采集下拉 options、section/subsection、context、稳定 selector 候选；
- 统一展开 Price/Stock、Product Description、Additional Description、Product Photos 等带 EDIT 的 section；
- Semantic Field Grouping：优先稳定 id，其次去除 name 中 `_0_value` / `_1_value` / qualifier 索引，把多值控件还原成一个真实属性；
- 不记录认证数据，不上传图片，不点击 Save / Send to QC。

### Probe 输出

```text
logs/makro-probe/
├─ makro-fields-时间.json
├─ makro-page-时间.png
└─ makro-dom-时间.html
```

真实探测产物与 browser profile 都已经被 `.gitignore` 排除。

## Answer Resolver

Resolver 的输入不是固定类目模板，而是**当前页面实时得到的 semantic fields**。

每个解析结果包含：

```text
attribute_key
label
status: resolved / needs_review / missing / conflict
answer / answer_values
qualifier
source_type / source_reference
evidence / confidence
option_match
detail
```

当前来源规则：

1. 标准商品表里的明确结构化值；
2. 客户 Question/Answer 文件里的明确答案；
3. 后续可以接入图片识别、知识库、供应商/官方页面提取器；
4. LLM 只能在已有证据基础上归纳，不能凭常识生成产品参数。

来源冲突不自动裁决；下拉框只做规范化后的唯一精确匹配，不做危险的模糊猜测。

### 标准商品表

```powershell
python makro_fill.py --product private_data/products.xlsx --sku ABC123 --dry-run
```

标准商品表要求存在 SKU 列，每一行代表一个商品，其他表头直接作为证据字段。

### 客户 Question/Answer 文件

支持类似：

```text
Question | Explanation | Answer
Model Number | ... | L11
Ports | ... | USB-C
Colour | ... | Black
```

运行：

```powershell
python makro_fill.py --product private_data/product-qa.xlsx --source-format qa --dry-run
```

也支持中文 `问题/属性/字段 + 答案/值` 等常见表头。

## 真实 Makro Dry-Run Fill

```powershell
python makro_fill.py --product private_data/product-qa.xlsx --source-format qa --dry-run
```

或标准商品表：

```powershell
python makro_fill.py --product private_data/products.xlsx --sku ABC123 --dry-run
```

程序会：

1. 打开/复用自动化 Edge；
2. 让用户进入真实 Add a Single Listing；
3. 动态扫描当前所有 semantic fields；
4. 从 `ProductSourceBundle` 解析全部字段；
5. 找到一个存在 `resolved` 答案的 section；
6. 只填写该 section 的可靠答案；
7. 每个字段立即 readback；
8. 停在 Save 前供人工检查；
9. 写入 `logs/makro-fill/makro-fill-*.json`；
10. **绝不点击 Save / Send to QC**。

可以用 `--section "Product Description"` 指定本次要测试的 section。

`--image`、`--product-url`、`--supplemental-text` 已进入统一 source bundle 接口，但当前版本不会自动从它们推断参数；图片识别/网页证据提取会作为下一层 provider 接入，避免在证据提取器尚未验证前偷偷猜值。

## 测试

```powershell
pytest -q
pytest -q -m probe
```

GitHub Actions：`tests` job 跑全部单元测试；`mock-e2e` job 跑原有 mock browser dry-run 并执行 probe 浏览器测试。

## 安全原则

项目默认采取“宁可漏填，不要错填”的策略：

- 不保存账号密码；
- 不绕过验证码或平台风控；
- 不把客户资料、Cookie、Token 提交 GitHub；
- 未验证字段不提交；
- 来源冲突进入人工复核；
- 下拉选项无法唯一精确匹配时不自动选择；
- 经营字段禁止 AI 猜测；
- 当前真实 Makro 仍只有 probe / no-save dry-run；
- `makro_fill.py` 代码路径中没有 Save / Send to QC 动作。

正式放入客户资料前，建议将仓库改为 **Private**。