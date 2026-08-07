# ecommerce-agent

电商卖家后台批量信息采集、匹配、填写与校验自动化原型。

当前阶段没有真实卖家后台，因此项目先用本地 `mock_site` 跑通最关键的闭环：

**读取商品表格 → 打开指定商品 → 核对 SKU → 抓取页面问题 → 匹配答案 → 自动填写 → 二次读取校验 → 保存 → 记录日志**

## 当前 V0.1 已完成

- 支持读取 `.csv`、`.xlsx`、`.xlsm` 商品表格。
- 按 SKU 批量处理多个商品。
- 使用 Playwright 控制 Chromium 浏览器。
- 从普通 HTML `<label>` 与输入控件关系中抓取页面问题。
- 对字段进行保守匹配：只接受精确匹配或明确配置的同义字段。
- 支持文本框、下拉框、复选框的基础填写。
- 每个字段填写后重新读取页面值进行校验。
- 商品 SKU 不一致时立即阻止填写。
- 必填字段找不到可靠答案时阻止保存。
- 字段校验失败时阻止保存。
- 支持 `--dry-run`：填写并校验，但不点击最终保存。
- 每个商品的执行结果写入 `logs/*.jsonl`。
- 内置假卖家后台与 3 个测试商品。
- GitHub Actions 自动执行基础单元测试。

## 项目结构

```text
ecommerce-agent/
├─ app/
│  ├─ data_loader.py       # CSV / Excel 读取
│  ├─ extractor.py         # 页面问题与控件抓取
│  ├─ matcher.py           # 问题 ↔ 表格字段匹配
│  ├─ filler.py            # 自动填写
│  ├─ validator.py         # 填写后二次校验
│  ├─ logger.py            # JSONL 日志
│  ├─ runner.py            # 批量执行与安全门
│  └─ platforms/
│     ├─ base.py           # 平台适配器接口
│     └─ mock.py           # 当前假卖家后台适配器
├─ data/
│  └─ products.csv         # 示例商品数据
├─ mock_site/
│  └─ index.html           # 本地假卖家后台
├─ scripts/
│  └─ create_sample_excel.py
├─ tests/
├─ logs/
├─ main.py
└─ requirements.txt
```

## Windows 本地运行

建议 Python 3.11+。

### 1. 克隆仓库

```powershell
git clone https://github.com/yuchenm1303-png/ecommerce-agent.git
cd ecommerce-agent
```

### 2. 创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. 安装依赖和 Chromium

```powershell
pip install -r requirements.txt
playwright install chromium
```

### 4. 启动假卖家后台

保持这个终端运行：

```powershell
python -m http.server 8000 --directory mock_site
```

浏览器访问 `http://127.0.0.1:8000/?sku=A001` 可以看到模拟商品编辑页面。

### 5. 开另一个终端运行自动化

先建议使用 dry-run：

```powershell
python main.py --dry-run
```

确认正常后执行完整保存：

```powershell
python main.py
```

程序会依次处理 `A001`、`A002`、`A003`，自动填写品牌、额定功率、色温、防水等级和外壳材质。

只测试一个商品：

```powershell
python main.py --limit 1 --dry-run
```

无头运行：

```powershell
python main.py --headless
```

## 测试 Excel

仓库保留 CSV 示例，不提交生成的二进制 Excel。需要 `.xlsx` 时运行：

```powershell
python scripts/create_sample_excel.py
python main.py --data data/products.xlsx --dry-run
```

## 为什么现在先做 mock 平台

真实电商网站拿到之后，Excel 读取、批处理、字段匹配、校验、安全门和日志不需要推倒重写。主要新增一个真实平台适配器，处理：

- 登录状态；
- 商品搜索或商品编辑链接；
- 页面特有的 DOM 结构；
- 动态下拉框、弹窗、iframe；
- 保存成功信号；
- 验证码或人工接管点。

## 下一阶段

1. 接入第一个真实卖家后台，新增 `app/platforms/<platform>.py`。
2. 增加登录状态持久化，密码、Cookie、Token 均不得提交 GitHub。
3. 支持更复杂的单选、多选、级联下拉、规格表格。
4. 建立商品问题答案知识库。
5. 当表格中没有答案时，从指定产品资料、说明书或官网检索答案。
6. 对检索答案记录来源与置信度；低置信度不自动提交。
7. 最后再增加 AI 对字段语义和异常页面的辅助判断。

## 安全原则

这个项目默认采取“宁可漏填，不要错填”的策略。未知字段不会使用模糊猜测强行匹配；商品身份、必填字段或填写结果任一环节无法验证时，程序都会阻止最终保存。

不要向仓库提交正式账号密码、Cookie、浏览器 `storage_state`、API Key 或客户商品敏感数据。当前仓库如果保持 Public，尤其要严格遵守这一点；正式接入客户环境前建议改成 Private。
