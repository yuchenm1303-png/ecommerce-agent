# AGENTS.md

## 项目目标

`ecommerce-agent` 是电商卖家后台批量信息采集、匹配、填写与校验自动化工具。
当前阶段聚焦 Makro Marketplace Seller Center：

`https://seller.makro.co.za` → `#dashboard/addListings/single`

核心流程：

读取商品资料 → 打开 Add Listing → 抓取页面问题 → 查找/生成可靠答案 →
自动填写 → 二次校验 → 人工/规则安全门 → 保存 → 记录日志

## 当前阶段（重要）

- 第一优先级是 `makro_probe.py` 真实 DOM 探测，把页面结构、字段、选项、
  内部滚动容器采集完整，产出 `logs/makro-probe/makro-fields-*.json`。
- `makro_probe.py` 默认使用本机 Microsoft Edge（`channel="msedge"`）和独立
  persistent profile `browser_profiles/makro-edge/`，绝不打开用户日常 Edge profile；
  `--browser chromium` 仅用于调试。
- Probe 流程：默认打开 Makro 首页 → 用户手动登录并从 UI 进入
  `Add a Single Listing` → 终端按 Enter → 直接采集当前页面。
  不强制 `page.goto` 回旧 `requestId` URL（可能已随 SPA 会话失效）。
  `--url` 可选，仅作初始导航/校验。
- 真实 Makro DOM 已采集：字段名称从 `.styles__AttributeItemLabelName...` 提取，
  必填通过 `sup.mandatory-star__MandatoryStarContainer` 检测
  （`required_hint="mandatory-star"`）；不得把 label/required 检测降级成
  依赖 error/context 文本。
- `--scan-sections`：逐 section 扫描，可点击 EDIT 展开 Product Description /
  Additional Description / Product Photos 后单独滚动扫描；只点安全 Cancel，
  不填写、不上传、不保存、不点 Send to QC。
- `--keep-open`：保持同一个 Edge 会话，登录一次后可反复扫描多个 Add Listing 页面；
  启动时检测登录状态，仍有效则不要求重新登录；每次扫描后询问是否继续，结束时询问
  是否保持浏览器打开。禁止记录 Cookie/token/sessionStorage/Authorization，
  禁止实现认证绕过。
- Semantic Field Grouping：DOM controls 按 Makro attribute 聚合成 semantic fields
  （优先稳定 id，其次 name 去索引，label 兜底）；不得硬编码任何类目的字段列表，
  多值字段必须只生成一个 semantic field。JSON 输出 `semantic_fields` +
  `semantic_field_count`，`control_count` 保持 DOM 控件数。
- `--scan-sections` 必须对所有带 EDIT 的 listing section（含 Price, Stock and
  Shipping Information）统一展开扫描；扫描结束只允许点安全 Cancel，禁止 Save /
  Send to QC，禁止上传文件。
- 真实 Makro 页面尚未验证字段映射之前：
  - 禁止点击 `Save`（对应参数 `--allow-save`，暂未实现）。
  - 禁止点击 `Send to QC`（对应参数 `--allow-submit`，暂未实现）。
  - 必须保留 `--dry-run`。
- 没有真实登录环境时，不得伪造“Makro 已经测试通过”。只做静态代码、
  单元测试、mock E2E，并在 README 中如实说明。

## 架构

- `app/`：核心业务代码。
  - `data_loader.py`：CSV/XLSX 商品表读取。
  - `extractor.py`：普通 `<label>` 表单字段提取（保守策略）。
  - `matcher.py`：字段匹配，只接受精确或明确别名，不做模糊猜测。
  - `filler.py` / `validator.py`：填写与读回校验。
  - `runner.py`：批量执行与 JSONL 日志。
  - `platforms/`：平台适配器（`base.py`、`mock.py`、`makro.py`）。
- `makro_probe.py`：登录后的真实 DOM 探测 CLI（只读）。
- `mock_site/`：本地 mock 卖家后台，供自动化测试。
- `tests/`：pytest 测试；`-m probe` 是需要本机 Chromium 的浏览器测试。

平台相关逻辑不要塞进通用 `extractor.py`，放进 `app/platforms/`。

## 安全规则（必须遵守）

永远不要 commit / 硬编码 / 输出：

- 邮箱、密码、Cookie、Token、API Key、localStorage/sessionStorage 内容。
- `browser_profiles/`、`storage_state*.json`、`.auth/`。
- `logs/makro-probe/` 真实探测产物、客户原始数据、压缩包。
- Makro 完整示例 URL 或临时 `requestId`；URL 必须由命令行/配置传入。

`.gitignore` 已覆盖上述路径，新增本地文件时保持同样策略。

### 防错原则

宁可不填，也绝对不要填错。以下情况必须阻止自动保存/提交：

- 无法确认当前页面；required 字段没有答案；匹配置信度太低；
- 多个答案来源冲突；字段类型不一致；dropdown 找不到精确选项；
- 填写后二次读取不一致；页面异常、网络失败、session 失效。

AI 不允许凭空生成商品技术规格（SKU/价格/库存/MOQ/运输等经营数据
必须来自用户 Excel、规则或明确配置）。

## 开发流程

每完成一块：

1. 写代码；
2. 写测试；
3. 本地运行 `pytest -q`（Windows：`.\.venv\Scripts\Activate.ps1` 后执行）；
4. 保证原有 mock 测试与 GitHub Actions（`tests` + `mock-e2e`）不被破坏；
5. 修复错误；
6. 更新 README。

## 常用命令

```powershell
python -m pytest -q            # 全部测试
python -m pytest -q -m probe   # 仅浏览器探测测试
python makro_probe.py --url "..."   # 真实 Makro DOM 探测（用户已登录后运行）
python makro_probe.py --scan-sections   # 逐 section 扫描（展开折叠字段后单独采集）
python makro_probe.py --keep-open       # 同一 Edge 会话反复扫描（推荐）
python main.py --dry-run       # 本地 mock 闭环（先启动 mock_site 的 http.server）
```