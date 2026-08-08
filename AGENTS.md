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

AI 负责跨语言理解、同义词、简单计数、商品规格含义、字段语义映射、多来源综合、冲突判断、READY/REVIEW/CONFLICT/MISSING，以及 unresolved 字段值得搜索什么。

禁止恢复 attribute-specific Python 商品规则：颜色别名、双镜头计数、G-Sensor、FOV、Vehicle Brand、SD Card、camera/cabin/rear、bracket、package/product dimension 自然语言规则、QA alias/matcher、deterministic synthesis promotion。

如果需要“理解商品是什么意思”，改 AI context/prompt，不写本地 `if/else`。

## Python 只守硬边界

允许的本地逻辑只有机械可验证边界：live field/schema/product/source identity、citation provenance、seller-operated business lock、current Makro option/qualifier/multi-value shape、GTIN checksum、numeric min/max、maxlength、Selling Price <= Base Price/MRP、MinOQ <= MaxOQ、DOM 唯一定位、React settled readback、Save/reopen persistence、Product Photos persistence、禁止 Send to QC。

## Live schema 是唯一目标

AI 回答当前 Makro live fields，不是客户 QA 问题集。客户 workbook 只是 Answer、SKU、selected variant、supplier URL、备注等商品资料。不要重新引入 QA→live matcher、alias config 或 section override。

## 本地 AI 性能模型

`makro_resolve_ai.py` 是唯一生产 AI Resolver。

**一个商品 + 全部本地 sources + 全部 live fields = 一次 multimodal call。**

Qwen3.5 Omni 默认生产配置：

- `structured_mode=auto` → `json_object`；
- thinking=false；
- streaming；
- JSON mode 不设置 `max_tokens`；
- 默认 `max_repair_attempts=0`；
- network/API/timeout 永不 semantic repair；
- `request_timeout_seconds` 是整个 AI 阶段的 wall-clock deadline；
- 每 15 秒输出 progress，并报告 connection / first output / complete 时间。

不要恢复 question batch×source、per-source model loop、source concurrency Resolver、semantic-fact intermediate layer、Answer Resolver / Resolution Engine、默认整商品 retry/repair。

### Compact output

模型对每个 live target 给一个简短 decision 以保证覆盖，但不回显 product identity、schema digest、source manifest digest、contract version 或本地 warnings。READY 只需要 value + 最小 citation；CONFLICT 才需要 alternatives；只有值得外查时才需要 search_queries。

## 一次 Web Enrichment

第一遍 unresolved 字段若 AI 给出 search_queries，使用 DashScope Qwen 最多追加一次 sourced web call，所有 gap 一起处理。

要求复用 `DASHSCOPE_API_KEY`，使用 `search_strategy=agent`、`enable_source=true`、JSON mode、相同 wall deadline。已 READY 字段和 seller business fields 冻结；web citation URL 必须真实属于本次 search sources；invented URL 不能授权 READY；web failure 保留 local packet；不做多 Agent / 无限循环。

正常预算：本地资料足够 1 call；需要联网 1 local + 1 web；相同 hot cache 可 0 call。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等只能来自明确 seller data：`structured / business / config / rule`。图片、supplier、普通 web search、AI 推理都无权生成这些运营值。

## Planner / Browser

`makro_plan_listing.py --scan-live-schema` 只读扫描，不 AI、不填、不 Save。

最终 planner 使用 `--decision-packet` 重建同一 Product Source Pack、strict rebind packet、检查当前 schema，再生成只读 Fill Plan。

`makro_preview_listing.py` 只执行已验证 Fill Plan，不重新解释商品语义。完整 persistence acceptance 使用 `--all-step3 --allow-section-save`。`--image` 只是 AI evidence；只有 `--upload-image` 上传 Product Photos。

复用长期 Edge/CDP。多 Add Listing tabs、vertical/schema/source/identity 不匹配、已有未保存 section 都 fail closed。CDP 消失不擅自重启 Edge，不关闭长期 Edge，不修改 browser profile，不覆盖已有非-placeholder 用户值。

## 关键代码边界

- `app/ai_decisions.py`：compact AI decision contract + provenance validation + cache
- `app/product_context.py`：canonical source-pack context
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：AI decisions → executable plan
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/providers/openai_compatible.py`：Qwen/compatible JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：单次 sourced web search + wall deadline
- `makro_resolve_ai.py`：唯一 AI Resolver
- `makro_plan_listing.py`：schema scan + read-only final planner
- `makro_preview_listing.py`：真实 browser acceptance

旧商品语义层不要恢复：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、value-normalization product rules、snapshot→semantic-fact mapping。

## 真实验收边界

旧 AI-first 实机版本曾在真实 M8 上发生两个本地模型调用总计约 509 秒、最终 JSON 解析失败。当前代码已经针对根因改成原生 JSON mode、JSON mode 无 max_tokens、默认无 full-product repair、真实 wall deadline、progress 和 compact contract，但这只能由下一次真实 Qwen 冷运行证明性能已改善。

在真实冷运行成功、coverage/耗时/来源质量检查通过之前，不进入真实 Step 3 persistence acceptance，也不要声称性能问题已经完成验收。

## Secrets / 客户数据

永远不要 commit、硬编码或输出密码、Cookie、Token、API Key、browser profile、真实客户原始文件/图片、临时 requestId。runtime `logs/*` 保持 ignored。

## 开发验收

正式修改后至少：`pytest -q`、GitHub Actions tests、mock-e2e、browser dry-run/probe 全部通过。真实写入前必须先检查 AI Decisions + read-only Fill Plan。PR 保持 Draft/unmerged，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。
