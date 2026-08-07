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
python makro_probe.py --url "你的 Add a Single Listing 完整网址"
```

第一次运行会打开一个独立的 Playwright Chromium 窗口。如果没有登录：

1. 在这个浏览器窗口里手动登录 Makro；
2. 打开 `Add a Single Listing`；
3. 回终端按 Enter；
4. 程序只采集表单元数据和页面截图，不提交商品。

输出在：

```text
logs/makro-probe/
├─ makro-fields-时间.json
└─ makro-page-时间.png
```

本地登录状态保存在：

```text
browser_profiles/makro/
```

`browser_profiles/`、`private_data/`、客户压缩包和 `logs/makro-probe/` 都已经加入 `.gitignore`。

下一步需要根据 `makro-fields-*.json` 确认 Makro 的真实控件结构，然后实现：

- `SKU ID`、Listing Status、价格、MOQ、库存/运输字段；
- Product Info 中不同 vertical 的动态属性；
- 单选、下拉、多选、可重复输入（`+`）等复杂控件；
- 每个字段填写后的读回校验；
- 分区 `Save` 的成功反馈；
- 最终 `Send to QC` 前的总校验。

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
