# AGENTS.md

## 唯一生产目标

`ecommerce-agent` 当前只认这一条 Makro Step 3 链：

`只读 Makro live schema`
→ `Product Source Pack`
→ `Product Profile`
→ `小批 live fields 并行映射`
→ `unresolved 并行 Web Research`
→ `一次 text-only Final Resolve`
→ `Thin Hard Guards`
→ `只读 Fill Plan`
→ `真实填写`
→ `section Save`
→ `reopen persisted verification`
→ `Product Photos persistence`

`Send to QC` 当前绝对禁止自动点击。

## AI 主导，本地商品规则极弱

AI 负责跨语言理解、同义词、计数、商品规格含义、scope 判断、字段语义映射、多来源综合和冲突判断。

禁止恢复 attribute-specific Python 商品规则：颜色别名、双镜头计数、G-Sensor、FOV、Vehicle Brand、SD Card、camera/cabin/rear、bracket、package/product dimension 自然语言规则、QA alias/matcher、deterministic synthesis promotion。

如果需要“理解商品是什么意思”，改 AI profile/mapping/final context，不写本地 `if/else`。

## Python 只守硬边界和调度

允许的本地逻辑：

- stage scheduling / batching / concurrency / cache；
- live field/schema/product/source identity；
- citation provenance；
- seller-operated business lock；
- current Makro option/qualifier/multi-value shape；
- GTIN checksum、numeric min/max、maxlength；
- Selling Price <= Base Price/MRP、MinOQ <= MaxOQ；
- DOM 唯一定位、React settled readback；
- Save/reopen persistence、Product Photos persistence；
- 禁止 Send to QC。

机械分批不是商品语义规则。Field/Web batch 只能按 live schema/target 顺序和固定 batch size 切片，不允许建立 camera/storage/dimension 等 Python 分类表。

## Stage 1 — Product Profile

`app/product_profile.py` 是唯一允许读取全部 raw images + raw customer/supplier evidence 的 AI 阶段。

它 **不接收 Makro live target fields**，只构建 compact product facts：identity、selected variant、scope、supported facts、conflicts 和原始 citations。

硬边界：

- unknown facts 省略；
- credible same-scope disagreement 保留 conflict candidates；
- packaging/product-body、cabin/rear、manual/UI language、product/compatible brand 不混 scope；
- negative fact 必须有明确负面证据；
- citation 必须回到原始 source id。

Product Profile cache 不依赖 Makro schema。图片/原始大文本在后续 stages 不再发送。

## Stage 2 — Parallel Field Mapping

`app/field_mapping.py` 只接收 Product Profile + 小批 live fields。

默认：

- batch size 12；
- concurrency 4。

每个 batch 独立 cache；单 batch 失败只让对应字段 unresolved，不得重跑整个商品。

Business fields 在进入 mapping 前过滤，并由最终 packet validator 强制 `business_locked`。

## Stage 3 — Parallel Web Research

只有 `MISSING / REVIEW / CONFLICT` 的非经营字段进入 Web Research。

默认：

- web batch size 5；
- concurrency 3；
- search model `qwen3.7-max`。

Web Research 只找 evidence，不直接做最终 field decision。URL 必须属于当前 Responses `web_search_call.action.sources`；invented URL 丢弃。

每个 research batch 独立 cache，失败只影响该 batch。

## Stage 4 — Final Resolve

只有实际获得 Web evidence 的 unresolved fields 进入一次 text-only final resolve。

输入只包含 Product Profile、对应 fields、accepted web evidence。禁止重新发送图片，禁止重新回答已 READY 字段。

最终仍输出 `ready / review / conflict / missing / business_locked`。

## 模型职责固定

默认：

- Product Profile / Field Mapping / Final Resolve：`qwen3.7-plus`
- Responses Web Research：`qwen3.7-max`

不要通过不断切换 Flash/Plus/Omni 掩盖架构问题。若以后换模型，必须有明确 A/B 数据或 provider 能力原因。

本地 Qwen JSON task 使用 JSON mode、thinking=false 和真实 wall-clock deadline。Web Search 不发送与 search tool 不兼容的 JSON response_format；它解析 JSON text，并独立校验真实 search sources。

## Cache

四层 cache：

1. Product Profile cache；
2. Field batch cache；
3. Web Research batch cache；
4. Final Resolve cache。

相同商品热运行应尽量 0 model calls。Makro schema 少量变化只使受影响 field batch 失效，不得迫使 Product Profile 重新看图片。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等只能来自明确 seller data：`structured / business / config / rule`。图片、supplier、普通 web search、AI 推理都无权生成这些运营值。

## Planner / Browser

`makro_plan_listing.py --scan-live-schema` 只读扫描，不 AI、不填、不 Save。

最终 planner 使用 `--decision-packet` 重建同一 Product Source Pack、strict rebind packet、检查当前 schema，再生成只读 Fill Plan。

`makro_preview_listing.py` 只执行已验证 Fill Plan，不重新解释商品语义。完整 persistence acceptance 使用 `--all-step3 --allow-section-save`。`--image` 只是 evidence；只有 `--upload-image` 上传 Product Photos。

复用长期 Edge/CDP。多 Add Listing tabs、vertical/schema/source/identity 不匹配、已有未保存 section 都 fail closed。CDP 消失不擅自重启 Edge，不关闭长期 Edge，不修改 browser profile，不覆盖已有非-placeholder 用户值。

## 关键代码边界

- `app/product_profile.py`：raw multimodal 商品理解 + profile cache
- `app/field_mapping.py`：compact profile → mechanical small batches + parallel mapping/cache
- `app/web_enrichment.py`：parallel evidence research + text-only Final Resolve
- `app/ai_decisions.py`：decision data/provenance/schema/source validation only
- `app/product_context.py`：canonical source-pack context
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：AI decisions → executable plan
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/providers/openai_compatible.py`：Qwen/compatible JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：Responses web_search + source provenance + wall deadline
- `makro_resolve_ai.py`：唯一 AI orchestration entrypoint
- `makro_plan_listing.py`：schema scan + read-only final planner
- `makro_preview_listing.py`：真实 browser acceptance

旧商品语义层和旧超级请求执行器禁止恢复：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、value-normalization product rules、snapshot→semantic-fact mapping、`run_ai_resolution()` whole-product field resolver。

## Secrets / 客户数据

永远不要 commit、硬编码或输出密码、Cookie、Token、API Key、browser profile、真实客户原始文件/图片、临时 requestId。runtime `logs/*` 保持 ignored。

## 开发验收

正式修改后至少：`pytest -q`、GitHub Actions tests、mock-e2e、browser dry-run/probe 全部通过。

真实 M8 验收必须一次性完成：Product Profile → parallel mapping → Web Research → Final Resolve → hot rerun → read-only Fill Plan。报告每个 stage 的 calls、batch count、cache hits、wall time、failed batches、profile conflicts、web URLs/evidence 和最终 coverage。

如果真实 Makro numeric/package fields 仍被 Planner 因 control metadata 阻止，只抓真实 controls/id/name/role/options/qualifier 信息后再修 DOM scanner；不要猜。

真实写入前必须检查 `product-profile.json + ai-decisions.json + read-only Fill Plan`。PR 保持 Draft/unmerged，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。