# ecommerce-agent

电商卖家后台批量信息采集、匹配、填写与校验自动化原型。

项目已经从本地 `mock_site` 进入第一个真实平台适配阶段：**Makro Marketplace Seller Center**。

核心目标：

**读取商品资料 → 打开 Add Listing → 抓取页面问题 → 查找/生成可靠答案 → 自动填写 → 二次校验 → 人工/规则安全门 → 保存 → 记录日志**

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

### V0.2：Makro 真实平台接入

- 新增 `app/platforms/makro.py`，校验 Makro Add a Single Listing hash route。
- 不依赖 `requestId` 作为长期稳定标识，因为该参数可能由平台动态生成。
- 新增 `makro_probe.py`：在用户自己的电脑上登录后，采集真实页面控件的 DOM 元数据。
- 使用本地持久化 Playwright 浏览器目录，账号密码不写入代码、不上传 GitHub。
- Probe 默认不记录输入框当前值，也不会点击 `Save` / `Send to QC`。
- Makro 最终保存目前故意保持禁用，等真实 DOM 结构和保存成功信号验证后再开放。

## 项目结构

```text
ecommerce-agent/
├─ app/
│  ├─ data_loader.py
│  ├─ extractor.py
│  ├─ matcher.py
│  ├─ filler.py
│  ├─ validator.py
│  ├─ logger.py
│  ├─ runner.py
│  └─ platforms/
│     ├─ base.py
│     ├─ mock.py
│     └─ makro.py          # Makro 真实平台守护适配器
├─ data/
│  └─ products.csv
├─ mock_site/
│  └─ index.html
├─ scripts/
├─ tests/
├─ logs/
├─ makro_probe.py          # 登录后采集真实 Makro DOM
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

## 先测试本地 mock

终端 1：

```powershell
python -m http.server 8000 --directory mock_site
```

终端 2：

```powershell
python main.py --dry-run
```


## Makro 真实页面：第一步先采集 DOM

不要把 Makro 邮箱、密码写进代码或发到仓库。

在 PowerShell 中运行：

```powershell
python makro_probe.py
```

程序默认打开 Makro 首页（`https://seller.makro.co.za/`），不会直接跳转旧的
Add Listing 深层 URL（旧 `requestId` 可能随 SPA 会话失效）。操作步骤：

1. 程序会自动检测登录状态：persistent profile 登录仍有效时直接复用，
   失效时才需要在自动化 Edge 窗口里手动登录；
2. 从页面正常进入 `Add a Single Listing`（保持在该页面）；
3. 回到终端按 Enter；
4. 程序直接采集当前页面（不会再强制 goto 旧 URL），只采集表单元数据、
   截图和安全的 DOM 快照，不提交商品；
5. 使用 `--keep-open` 时，扫描后询问“继续扫描下一个页面？ [Y/n]”，
   选择 Y 即可在同一 Edge 会话中反复扫描多个 Add Listing 页面（只需登录一次），
   全部结束后还会询问是否保持 Edge 打开。

### Probe 采集能力

- 默认使用本机 Microsoft Edge（`channel="msedge"`）和独立持久化 profile `browser_profiles/makro-edge/`，不接管日常 Edge，登录状态不出现在代码里；
- 启动时输出实际使用的 `user_data_dir`（始终是 `browser_profiles/makro-edge` 的绝对路径）并确认持久化目录存在；
- 启动时自动检测登录状态：登录仍有效则直接使用，不要求重新登录；
- 扫描整个 DOM，识别 `input` / `textarea` / `select` / `[role="combobox"]` /
  自定义 dropdown / checkbox / radio / autocomplete 等控件；
- 自动遍历页面窗口和所有内部滚动容器，逐步滚动等待懒加载，避免漏掉
  viewport 之外的字段；
- 字段名称优先从 Makro 真实 label 结构提取（`.styles__AttributeItemLabelName...`），
  不依赖 error/context 文本；required 通过字段 wrapper 内的
  `sup.mandatory-star__MandatoryStarContainer` 检测，并写入
  `required_hint="mandatory-star"`；
- 对每个字段采集：显示名称、required、控件类型、id、name、aria-label、
  aria-labelledby、placeholder、role、可用选项、所属 section、周围 context
  文本、稳定 selector 候选；
- 不读取密码/隐藏字段；默认不记录输入值；`--include-values` 仅用于调试；
- 不记录 Cookie、token、sessionStorage、Authorization 等认证数据（DOM 快照会清洗敏感属性）；
- 默认不点击 `Save` / `Send to QC`。
- `--scan-sections` 逐 section 扫描：对所有带 EDIT 的 listing card 使用统一策略——
  点击 EDIT 展开（含 Price, Stock and Shipping Information / Product Description /
  Additional Description / Product Photos），等待字段渲染后单独滚动扫描，为每个字段
  写入 `section_heading`；只点安全的 Cancel 收起，不填写、不上传、不保存、
  不点 Send to QC。
- Semantic Field Grouping：把 DOM controls 按 Makro attribute 聚合（优先稳定 id，
  其次从 name 去除 `_0_value` / `_1_value` 等重复索引；label 仅兜底），多值字段
  只生成一个 semantic field，内部包含全部 controls；JSON 新增 `semantic_fields` 与
  `semantic_field_count`（真实属性数），`control_count` 仍表示 DOM 控件数。

### 常用参数

```text
--url               可选。仅作为初始导航/校验；Enter 后采集当前页面，不再强制跳转
--browser           edge（默认）/ chromium（调试用）
--profile-dir       默认 browser_profiles/makro-edge（Edge 独立目录）
--include-values    调试时记录当前输入值（默认关闭）
--open-dropdowns    尝试点击自定义下拉框读取弹出选项（可能有轻微副作用）
--scan-sections    统一展开所有带 EDIT 的 section 后逐 section 扫描（含 Price/Stock）
--keep-open        同一 Edge 会话反复扫描：每次扫描后询问是否继续，结束时询问是否保持打开
--no-dom-snapshot   不生成 makro-dom-*.html
--headless          无头模式（仅 profile 已登录时使用）
--scroll-wait-ms    滚动后等待懒加载的毫秒数（默认 350）
--max-scroll-steps  单个滚动容器的滚动次数上限（默认 200）
```

例如：

```powershell
# 完整采集（推荐）：打开首页 → 登录 → 进入 Add Listing → Enter
python makro_probe.py

# 已有 Add Listing URL 时，也可作为初始导航传入（Enter 后仍采集当前页面）
python makro_probe.py --url "你的完整网址"

# 调试时用 Playwright 内置 Chromium
python makro_probe.py --browser chromium

# 调试时连下拉框选项也读出来
python makro_probe.py --open-dropdowns

# 采集全部 section（含折叠的 Product Description / Additional Description / Product Photos）
python makro_probe.py --scan-sections

# 推荐：单次登录后在同一 Edge 会话中反复扫描多个 Add Listing 页面
python makro_probe.py --keep-open
```

### 输出文件

```text
logs/makro-probe/
├─ makro-fields-时间.json   字段元数据（含控件、真实 label、required、section、selector、
│                          semantic_fields 分组与 semantic_field_count；
│                          --scan-sections 时另含按 section 分组的 sections 列表）
├─ makro-page-时间.png      整页截图
└─ makro-dom-时间.html      安全的 DOM 快照（已去掉脚本内容、输入值和敏感属性）
```

本地登录状态保存在：

```text
browser_profiles/makro-edge/
```

`browser_profiles/`、`storage_state*.json`、`private_data/`、客户压缩包和
`logs/makro-probe/` 都已经加入 `.gitignore`，真实探测数据不会提交 GitHub。

真实 Makro DOM 探测已跑通：字段真实名称、mandatory-star 必填标记、semantic field
聚合（如 sports_action_camera 的 Product Description 36 个属性 vs 50 个 DOM 控件）
都已正确提取；Price/Stock 等所有带 EDIT 的 section 可由 `--scan-sections` 统一展开
扫描。下一步基于 `makro-fields-*.json` 实现：

- `SKU ID`、Listing Status、价格、MOQ、库存/运输字段；
- Product Info 中不同 vertical 的动态属性；
- 单选、下拉、多选、可重复输入（`+`）等复杂控件；
- 每个字段填写后的读回校验；
- 分区 `Save` 的成功反馈；
- 最终 `Send to QC` 前的总校验。

## 测试

```powershell
pytest -q          # 单元测试 + 可用的浏览器探测测试
pytest -q -m probe # 仅运行浏览器探测测试（需要本机安装 Playwright Chromium）
```

GitHub Actions：`tests` job 跑 `pytest -q`；`mock-e2e` job 保留原 mock 浏览器
自动化，并额外运行 `pytest -q -m probe` 覆盖探测逻辑。

## 商品答案引擎规划

客户现有人工流程通常是：产品链接/图片/规格资料 + 问题模板 → 人工交给大模型分析 → 把答案再填回后台。

自动化版本会拆成：

1. **Question Schema**：从 Makro 页面/问题模板得到标准问题、类型、单位和可选项。
2. **Evidence Store**：保存产品图片、供应商页面、说明书、已有 Excel 等来源。
3. **Answer Resolver**：优先从明确资料找答案，再进行语义匹配/AI 推理。
4. **Confidence Gate**：来源冲突或低置信度时不自动填写。
5. **Browser Executor**：Playwright 精确写入对应控件。
6. **Validator**：重新读取页面值，确认无误后才允许保存。

AI 负责“理解问题和资料”，Playwright 负责“精确执行”，避免纯视觉 Agent 直接猜位置。

## 安全原则

项目默认采取“宁可漏填，不要错填”的策略：

- 不保存账号密码；
- 不绕过验证码或平台风控；
- 不把客户资料、Cookie、Token 提交 GitHub；
- 未验证字段不提交；
- 来源冲突时进入人工复核；
- 真实平台最初始终使用 dry-run / probe 模式。

当前仓库如果保持 **Public**，正式放入客户资料之前强烈建议改成 **Private**。