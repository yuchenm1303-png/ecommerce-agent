# AGENTS.md

## 唯一生产目标

`ecommerce-agent` 当前只认这一条 Makro Step 3 链：

`只读 Makro live schema`
→ `Product Source Pack`
→ `一次整商品 AI 本地填空`
→ `必要时一次有来源 Web 补空`
→ `Thin Hard Guards`
→ `只读 Fill Plan`
→ `真实填写`
→ `section Save`
→ `reopen persisted verification`
→ `Product Photos persistence`

`Send to QC` 当前绝对禁止自动点击。

## AI 主导，本地商品规则极弱

AI 负责：

- 跨语言理解、同义词、简单计数；
- 商品规格含义；
- 字段与商品描述的语义映射；
- 多来源综合；
- 冲突判断；
- READY / REVIEW / CONFLICT / MISSING；
- unresolved 字段值得搜索什么。

禁止恢复任何 attribute-specific Python 商品规则，例如颜色别名、双镜头计数、G-Sensor、FOV、Vehicle Brand、SD Card、camera/cabin/rear、bracket、package/product dimension 自然语言规则、QA alias/matcher、deterministic synthesis promotion。

如果需要“理解商品是什么意思”，优先修改 AI context/prompt，不要写本地 `if/else`。

## Python 只守硬边界

允许的本地逻辑必须是机械可验证的：

- live field/schema/product/source identity；
- citation provenance；
- seller-operated business field lock；
- current Makro option / qualifier / multi-value shape；
- GTIN checksum；
- numeric min/max；
- maxlength；
- Selling Price <= Base Price/MRP；
- MinOQ <= MaxOQ；
- DOM 唯一定位；
- React settled readback；
- Save/reopen persistence；
- Product Photos persistence；
- `Send to QC` 禁止。

不要为了 coverage 放松这些硬边界。

## Live schema 是唯一目标 schema

AI 要回答的是当前 Makro live fields，不是客户 QA 问题集。

客户 workbook 只是高价值商品资料：Answer、SKU、selected variant、supplier URL、备注、其他结构化信息。

不要重新引入 QA→live matcher、alias config 或 section override。

## AI 本地填空性能模型

`makro_resolve_ai.py` 是唯一生产 AI Resolver。

正常路径：

**一个商品 + 全部本地 sources + 全部 live fields = 一次 multimodal call**

Qwen3.5 Omni 默认生产配置：

- `structured_mode=auto` 自动解析为 `json_object`；
- thinking=false；
- streaming；
- JSON mode 不设置 `max_tokens`；
- 默认 `max_repair_attempts=0`；
- 网络/API/timeout 永远不 semantic repair；
- `request_timeout_seconds` 是整个 AI 阶段的 wall-clock deadline；
- 每 15 秒输出进度，并显示 connection / first output / complete 时间。

不要恢复：

- question batch × source；
- per-source model loop；
- source concurrency Resolver；
- semantic-fact intermediate layer；
- Answer Resolver / Resolution Engine；
- 默认整商品 retry/repair。

### Compact model output

模型只输出真正需要的字段 decisions。不要再要求模型回显：

- product identity；
- schema digest；
- source manifest digest；
- contract version；
- 本地 warnings。

这些数据 Python 已知，最终 packet 由程序自己附回。

正常字段也不要要求冗长解释。READY 需要 value + 最小 citation；CONFLICT 才需要 alternatives；MISSING/REVIEW 只有值得联网时才需要 search_queries。

## 一次 Web Enrichment

第一遍 unresolved 字段若 AI 给出 search_queries，使用 DashScope Qwen 最多追加一次 sourced web call，所有 gap 一起处理。

要求：

- 复用 `DASHSCOPE_API_KEY`；
- `search_strategy=agent`；
- `enable_source=true`；
- JSON mode；
- 同样受 wall-clock deadline 限制；
- 已 READY 字段冻结；
- seller business fields 冻结；
- web citation URL 必须真实属于本次返回 search sources；
- invented URL 不能授权 READY；
- web failure 保留有效 local packet；
- 不做多 Agent / 无限搜索循环。

正常调用预算：

- 本地资料足够：1 call；
- 需要联网：1 local + 1 web；
- 相同输入 hot cache：0 call。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等只能来自明确 seller data：

`structured / business / config / rule`

图片、supplier、普通 web search、AI 推理都无权生成这些运营值。

## Planner

`makro_plan_listing.py` 只有两种模式：

1. `--scan-live-schema`：只读扫描，生成 live-schema.json；不 AI、不填、不 Save。
2. `--decision-packet ...`：重建同一 Product Source Pack，strict rebind decision packet，检查当前 schema，生成只读 Fill Plan；不写页面。

不要创建第二个 planner wrapper。

## Browser executor

`makro_preview_listing.py` 只执行已经验证的 Fill Plan，不重新解释商品语义。

完整 persistence acceptance：

`--all-step3 --allow-section-save`

每个 section：定位唯一字段 → 检查已有值 → 写入 → React settled readback → Save → collapse → reopen → persisted readback → 报告。

Product Photos：`--image` 只作为 AI evidence；只有 `--upload-image` 上传。staged 不等于 persisted。永不 Send to QC。

## Browser safety

- 复用长期 Edge/CDP 登录态；
- 多 Add Listing tabs → fail closed；
- vertical/schema/source/identity 不匹配 → fail closed；
- CDP 消失不擅自重启 Edge；
- 不关闭长期 Edge；
- 不修改 browser profile；
- 已有未保存 section → full acceptance 停止；
- 不覆盖当前非-placeholder 用户值。

## 关键代码边界

- `app/ai_decisions.py`：compact AI decision contract + provenance validation + cache
- `app/product_context.py`：canonical source-pack context
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：AI decisions → executable plan
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/providers/openai_compatible.py`：compatible/Qwen JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：单次 sourced web search + wall deadline
- `makro_resolve_ai.py`：唯一 AI Resolver
- `makro_plan_listing.py`：schema scan + read-only final planner
- `makro_preview_listing.py`：真实 browser acceptance

已经删除的旧商品语义层不要恢复：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、value-normalization product rules、snapshot→semantic-fact mapping。

## Secrets / 客户数据

永远不要 commit、硬编码或输出密码、Cookie、Token、API Key、browser profile、真实客户原始文件/图片、临时 requestId。runtime `logs/*` 保持 ignored。

## 开发验收

正式修改后至少：

1. `pytest -q`
2. GitHub Actions tests 通过
3. mock-e2e 通过
4. browser dry-run/probe 通过
5. 真实 Qwen/Makro 最终由用户本机环境验证
6. 真实写入前必须先检查 AI Decisions + read-only Fill Plan
7. PR 保持 Draft/unmerged，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成
