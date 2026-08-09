# AGENTS.md

## 唯一生产目标

`ecommerce-agent` 当前只认这一条 Makro Step 3 链：

`只读 Makro live schema`
→ `Product Source Pack`
→ `Product Profile（原始资料只理解一次）`
→ `Local Fill（现有资料先填）`
→ `Web Fill（只搜仍为空的字段并直接补入）`
→ `Thin Hard Guards`
→ `只读 Fill Plan`
→ `真实填写`
→ `section Save`
→ `reopen persisted verification`
→ `Product Photos persistence`

`Send to QC` 当前绝对禁止自动点击。

核心业务原则只有一句：**现有资料能确定的先填；只有仍为空的商品字段才上网搜；搜到就补，搜不到就留空，真实冲突就保留冲突。不要再增加一层 AI 复核或 Python 商品语义复核。**

## AI 主导，本地商品规则极弱

AI 负责跨语言理解、商品规格含义、selected variant、scope、字段语义映射、Web 搜索结果理解和冲突判断。

禁止恢复 attribute-specific Python 商品规则：颜色别名、双镜头计数、G-Sensor、FOV、Vehicle Brand、SD Card、camera/cabin/rear、bracket、package/product dimension 自然语言规则、QA alias/matcher、deterministic synthesis promotion。

如果需要“理解商品是什么意思”，改 Product Profile / Local Fill / Web Fill 的 AI context，不写本地商品 `if/else`。

## Python 只守机械边界和调度

允许的本地逻辑：

- stage scheduling / batching / concurrency / cache；
- live field/schema/product/source identity；
- citation/source provenance；
- seller-operated business lock；
- current Makro option/qualifier/multi-value shape；
- GTIN checksum、numeric min/max、maxlength；
- Selling Price <= Base Price/MRP、MinOQ <= MaxOQ；
- DOM 唯一定位、React settled readback；
- Save/reopen persistence、Product Photos persistence；
- 禁止 Send to QC。

机械分批不是商品语义规则。Local/Web batch 只能按 live schema/target 顺序和固定 batch size 切片，不允许建立 camera/storage/dimension 等 Python 分类表。

## Stage 1 — Product Profile

`app/product_profile.py` 是唯一读取全部 raw images + raw customer/supplier evidence 的 AI 阶段。

它 **不接收 Makro live target fields**，只构建 compact product facts：identity、selected variant、scope、supported facts、conflicts 和原始 citations。

要求：

- unknown facts 省略；
- credible same-scope disagreement 保留 conflict candidates；
- packaging/product-body、cabin/rear、manual/UI language、product/compatible brand 不混 scope；
- negative fact 必须有明确负面证据；
- citation 必须回到原始 source id。

Product Profile cache 不依赖 Makro schema。图片/原始大文本在后续 stages 不再发送。

## Stage 2 — Local Fill

`app/field_mapping.py` 只接收 Product Profile + 小批 live fields。

默认：

- batch size 12；
- concurrency 4；
- model `qwen3.7-plus`。

这一步就是“用现有资料先填表”。AI 正常输出：

- `READY`：现有资料已能确定；
- `CONFLICT`：现有资料对这个字段存在真实冲突；
- `MISSING`：现有资料不能确定，交给 Web Fill。

Local Fill prompt 不应主动制造 REVIEW。若 packet/citation 结构有问题，统一 validator 仍可机械降为 REVIEW；这种字段也允许 Web Fill 尝试补齐。

Local `READY` 和 `CONFLICT` 一旦形成，后续 Web 不重新搜索、不推翻。

每个 batch 独立 cache；单 batch 失败只让对应字段 unresolved，不得重跑整个商品。

Business fields 在进入 Local Fill 前过滤，并由 packet validator 强制 `business_locked`。

## Stage 3 — Web Fill

只有 Local Fill 后仍为 `MISSING / REVIEW` 的非经营字段进入 Web。

默认：

- web batch size 5；
- concurrency 3；
- search model `qwen3.7-max`。

Web Fill 是一次动作：**搜索该空字段，并直接返回字段结果**。不再存在“Web Research 后再交给另一个 Final Resolve”的层。

Web AI 对每个目标字段直接输出：

- `READY`：网络资料能确定；
- `CONFLICT`：可信网络来源对同一字段真正冲突；
- `MISSING`：搜索后仍不能确定。

URL 必须属于当前 Responses `web_search_call.action.sources`；invented URL 丢弃。READY 必须有真实 returned URL 对应的 citation；CONFLICT 每个 alternative 都要有自己的 returned URL/citation。

Web 只允许补 unresolved 字段。无效/不完整 Web 输出不能覆盖原来的 local packet。

每个 Web batch 独立 cache；失败只影响该 batch。

## 不再存在 Final Resolve

生产链中没有独立 `Final Resolve`、`final_provider`、`_run_final_resolution` 或 Final Resolve cache。

最终 `ai-decisions.json` 就是一张持续合并的字段表：

`Local READY/CONFLICT` + `Web 对空字段的有效补充` + `仍然 MISSING/REVIEW` + `BUSINESS_LOCKED`。

不要再增加“最后再让 AI 审一次全部字段”的层，也不要让 Python 重新理解商品语义。

## 模型职责固定

- Product Profile / Local Fill：`qwen3.7-plus`
- Web Fill：`qwen3.7-max`

不要通过不断切换 Flash/Plus/Omni 掩盖链路问题。若以后换模型，必须有明确 A/B 数据或 provider 能力原因。

本地 Qwen JSON task 使用 JSON mode、thinking=false 和真实 wall-clock deadline。Web Search 解析 JSON text，并独立校验真实 search sources。

## Cache

三层 cache：

1. Product Profile cache；
2. Local field batch cache；
3. Web Fill batch cache。

相同商品热运行应尽量 0 model calls。Makro schema 少量变化只使受影响 Local field batch / 对应 Web Fill 失效，不得迫使 Product Profile 重新看图片。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等只能来自明确 seller data：`structured / business / config / rule`。图片、supplier、普通 web search、AI 推理都无权生成这些运营值。

这类字段若缺值，应明确报告为 BUSINESS_LOCKED/业务输入缺失；不要拿商品搜索结果填，也不要为了通过 Save 编造默认值。

## Thin Hard Guards / Fill Plan

`app/ai_decisions.py` 只做 decision data/provenance/schema/source validation。不得用 SKU 关键词、negative 正则、dimension 正则等方式重新判断商品事实对错。

`app/fill_plan.py` 只把字段结果变成可执行计划，保留纯机械检查：

- single-value 字段不能接多个 values；
- Makro 有 option 时必须精确匹配真实 option；
- AI 返回 qualifier 时页面必须真有 qualifier 控件并匹配；
- READY 必须有可执行 value；
- GTIN/numeric/maxlength 等硬约束；
- business relation 硬约束。

## Planner / Browser

`makro_plan_listing.py --scan-live-schema` 只读扫描，不 AI、不填、不 Save。

最终 planner 使用 `--decision-packet` 重建同一 Product Source Pack、strict rebind packet、检查当前 schema，再生成只读 Fill Plan。

`makro_preview_listing.py` 只执行已验证 Fill Plan，不重新解释商品语义。完整 persistence acceptance 使用 `--all-step3 --allow-section-save`。`--image` 只是 evidence；只有 `--upload-image` 上传 Product Photos。

复用长期 Edge/CDP。多 Add Listing tabs、vertical/schema/source/identity 不匹配、已有未保存 section 都 fail closed。CDP 消失不擅自重启 Edge，不关闭长期 Edge，不修改 browser profile，不覆盖已有非-placeholder 用户值。

### Section Save

Save 不得绕过 Makro validation。只有当前 card 没有可见 validation error，点击 Save 后确实恢复 EDIT，且 collapsed card 没有 Error badge，才算保存成功。

如果 Price/Product Description 因 required 字段仍空而不能 Save，应回到上游字段覆盖或明确业务输入缺失处理，不要改浏览器去强行 Save。

### Product Photos

`input[type=file].files > 0` 只说明浏览器接收了文件选择，**不等于 Makro 接受图片**。

只有 Product Photos card 出现新增可见图片预览/新图片 source/完成计数增长之一，才能记为 staged 并允许随后 Save。Save 后还必须验证 collapsed `Product Photos (N/5)` 计数按预期增加。

## 关键代码边界

- `app/product_profile.py`：raw multimodal 商品理解 + profile cache
- `app/field_mapping.py`：compact profile → Local Fill 小批并行 + cache
- `app/web_enrichment.py`：只搜 unresolved 并直接 Web Fill + cache
- `app/ai_decisions.py`：decision data/provenance/schema/source validation only
- `app/product_context.py`：canonical source-pack context
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：field decisions → executable plan
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/providers/openai_compatible.py`：Qwen/compatible JSON transport + wall deadline/progress
- `app/providers/dashscope_web_search.py`：Responses web_search + real source provenance + wall deadline
- `makro_resolve_ai.py`：唯一 AI orchestration entrypoint
- `makro_plan_listing.py`：schema scan + read-only planner
- `makro_preview_listing.py`：真实 browser persistence acceptance
- `app/makro/photos.py`：Product Photos accepted-stage + persisted-count verification

旧商品语义层和旧超级请求执行器禁止恢复：Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、value-normalization product rules、snapshot→semantic-fact mapping、`run_ai_resolution()` whole-product field resolver，以及独立 Final Resolve 层。

## Secrets / 客户数据

永远不要 commit、硬编码或输出密码、Cookie、Token、API Key、browser profile、真实客户原始文件/图片、临时 requestId。runtime `logs/*` 保持 ignored。

## 开发验收

正式修改后至少：`pytest -q`、GitHub Actions tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实 M8 下一次先做一次冷/热 Resolver + read-only Fill Plan，不进行 Makro 写入。重点报告：

- Product Profile 是否继续正确保存 selected variant、包装/机身 scope 和真实 conflicts；
- Local Fill READY/CONFLICT/MISSING；
- Web 实际只收到哪些空字段；
- Web Fill 新增多少 READY/CONFLICT，真实 source URLs/evidence；
- 最终字段覆盖率和 required blocked；
- business locked 中哪些 required 字段仍需 seller 输入；
- packaging numeric fields 是否仍因真实 DOM qualifier/control metadata 被 Planner 阻止。

如果真实 Makro numeric/package fields 仍被 Planner 因 control metadata 阻止，只抓真实 controls/id/name/role/options/label/help/qualifier 信息后再修 DOM scanner；不要猜。

真实写入前必须检查 `product-profile.json + ai-decisions.json + read-only Fill Plan`。PR 保持 Draft/unmerged，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。