# AGENTS.md

## 唯一生产链

`ecommerce-agent` 当前只认这一条 Makro Step 3 链：

`1688/供应商商品链接 + 指定 SKU`
→ `独立 source Edge 自动采集当前商品页`
→ `AI 直接填写 Makro live fields`
→ `Web 只补仍为空的字段`
→ `Thin Hard Guards`
→ `只读 Fill Plan`
→ `真实填写`
→ `section Save`
→ `reopen persisted verification`
→ `Product Photos persistence`

`Send to QC` 当前绝对禁止自动点击。

核心原则：**资料写什么就填什么；空字段才搜索；搜不到就留空；真实冲突就保留冲突。不要再增加中间商品事实层、Final Resolve、Python 商品语义审核或循环复核。**

## 第一入口：从商品链接自动收集资料

客户原来的人工方法是从 1688 商品链接开始，确认 SKU，然后截图属性、包装、详情页再交给 AI。现在这一步由程序自动完成。

`app/source_capture.py` 只做机械采集：

- 使用独立 `browser_profiles/source-edge` / CDP 9333；
- 自动打开/复用当前商品页；
- 自动滚动触发 lazy content；
- 保存 rendered text、table/dl rows、JSON-LD、meta；
- 保存 full-page screenshot；
- 不解释任何商品事实；
- 不接触 Makro browser profile；
- 不绕过 CAPTCHA/风控。出现验证就停，让用户合法人工完成后用 `--source-use-current-page` 继续。

`makro_resolve_ai.py --product-url ...` 会把这次 capture 的 snapshot + screenshot 直接加入同一次本地 AI evidence。

现有 `--supplier-snapshot`、`--image`、`--facts-json` 等仍可作为额外资料，但新主流程不要求用户先手工做半成品素材包。

## AI 直接填 Makro fields

生产链已经不再调用 Product Profile。

`app/field_mapping.py` 直接接收：

- exact supplier page snapshot；
- exact supplier page screenshot；
- 客户 QA/已有答案；
- 额外图片/structured facts；
- 小批 Makro live fields。

AI 正常只输出：

- `READY`：当前资料明确支持；
- `CONFLICT`：当前资料对同一字段真实冲突；
- `MISSING`：当前资料不能确定。

Local `READY` / `CONFLICT` 一旦形成，后续 Web 不重搜、不推翻。

机械分批只按 live schema 顺序和固定 batch size 切片。禁止建立 camera/storage/dimension 等 Python 商品分类表。

## Web 只补空字段

`app/web_enrichment.py` 只处理 Local Fill 后仍为 `MISSING / REVIEW` 的非经营字段。

Web prompt 使用：

- `source_product_url` 作为第一身份锚点；
- 已经确定的 local READY/CONFLICT 作为当前商品/variant 指纹；
- 当前 unresolved Makro fields。

Web 先尝试当前 exact product URL；只有仍缺时才搜索其他来源。其他网页仅凭同一个通用型号名不足以认定同款。

Web 模型在同一次调用中完成“搜索 + 回答字段”，直接返回 `READY / CONFLICT / MISSING`。没有 Final Resolve。

URL 必须来自本次真实 `web_search` returned sources；模型编造 URL 不得进入最终字段表。

## AI 主导，本地代码不抢方向盘

AI 负责：

- 跨语言理解；
- selected variant；
- package/body/mount 等 scope；
- cabin/rear；
- manual/UI language；
- 字段语义映射；
- Web 同款判断；
- 冲突判断；
- 文案生成。

禁止恢复 attribute-specific Python 商品规则：颜色别名、双镜头计数、G-Sensor、FOV、Vehicle Brand、SD Card、camera/cabin/rear、bracket、package/product dimension 自然语言规则、QA alias/matcher、negative-feature inference、deterministic synthesis promotion。

如果商品含义判断错，优先修 AI 输入资料质量、field context 或搜索策略；不要用本地 `if/else` 替 AI 做商品判断。

## Python 只守机械边界

允许的本地逻辑：

- source capture；
- batching / concurrency / cache；
- live schema / product / source identity；
- citation provenance；
- seller business lock；
- current Makro option / qualifier / multi-value shape；
- GTIN checksum、numeric min/max、maxlength；
- Selling Price <= Base Price/MRP、MinOQ <= MaxOQ；
- DOM 唯一定位、React readback；
- Save/reopen persistence；
- Product Photos accepted-stage / persisted-count verification；
- 禁止 Send to QC。

Python 不判断“这个网页是不是同款”“manual language 是否等于 UI language”“front+cabin 是否等于 rear”“这个尺寸属于包装还是机身”。这些是 AI 任务。

## Business fields

SKU、Listing Status、Price、Stock、MOQ、Fulfilment、Shipping SLA、Selling Region 等是 seller-operated data，不是商品搜索题。

只能来自明确 `structured / business / config / rule` 输入。缺值就报告 BUSINESS_LOCKED/业务输入缺失，不允许 Web 或 AI 猜价格、库存、发货策略。

## Fill Plan / Browser

`makro_plan_listing.py --scan-live-schema`：只读扫描，不 AI、不填、不 Save。

最终 planner 只把 `ai-decisions.json` 变成机械可执行 Fill Plan。

`makro_preview_listing.py` 不重新理解商品，只执行已验证计划：

`READY → fill → React readback → Save → reopen → persisted verification`

已有非-placeholder 用户值不覆盖；live schema/source/product identity drift fail closed；绝不自动点击 `Send to QC`。

### Section Save

不绕过 Makro required validation。只有当前 card 无可见 validation error、Save 后确实恢复 EDIT、collapsed card 无 Error badge，才算保存成功。

### Product Photos

`input[type=file].files > 0` 不等于 Makro 接受图片。

只有出现新增可见预览、新图片 source 或 completion counter 增长，才记为 staged。Save 后还必须验证 collapsed `Product Photos (N/5)` 计数增长。

## Cache

现在生产 Resolver 只有两类 semantic cache：

1. Local field batch cache；
2. Web Fill batch cache。

source page 自己每次可重新 capture；相同 capture/content + 相同 schema 热运行应尽量 0 model calls。

## 模型

- Local Fill：`qwen3.7-plus`
- Web Fill：`qwen3.7-max`

不要为了掩盖链路问题频繁换模型。若换模型，需要明确能力或 A/B 证据。

## 关键文件

- `app/source_capture.py`：exact supplier page mechanical capture
- `app/source_snapshot.py`：页面 snapshot 结构
- `app/semantic_grounding.py`：raw source/citation manifest
- `app/field_mapping.py`：raw evidence → Local Fill
- `app/web_enrichment.py`：unresolved-only Web Fill
- `app/ai_decisions.py`：decision/provenance/schema validation
- `app/business_fields.py`：seller business policy
- `app/hard_field_validators.py`：纯机械 hard guards
- `app/fill_plan.py`：field decisions → executable plan
- `app/makro/photos.py`：Product Photos persistence
- `makro_resolve_ai.py`：唯一 Resolver orchestration entrypoint
- `makro_plan_listing.py`：live schema / read-only planner
- `makro_preview_listing.py`：真实 browser persistence acceptance

`app/product_profile.py` 可暂时保留用于历史兼容/测试，但**不属于生产 Resolver 路径**。禁止重新把它接回 `makro_resolve_ai.py`。

旧 Answer Resolver、Resolution Engine、semantic-fact runner、QA matcher、alias config、whole-product `run_ai_resolution()`、独立 Final Resolve 都不应恢复。

## Secrets / 客户数据

永远不要 commit、硬编码或输出密码、Cookie、Token、API Key、browser profile、真实客户原始文件/图片、临时 requestId。runtime `logs/*` 保持 ignored。

## 开发验收

代码修改至少要求 GitHub Actions：unit tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品下一轮先做：

`product URL capture → cold Resolver → hot Resolver → read-only Fill Plan`

不要直接写 Makro。重点检查：

- exact 1688 页面是否确实采集到参数/包装/详情；
- Local Fill READY/CONFLICT/MISSING；
- Web 是否只收到 unresolved；
- Web 是否优先使用 exact product URL / 当前商品指纹；
- 最终 required blocked 中哪些是商品资料、真实 conflict、business input、DOM constraint；
- packaging numeric fields 若仍被 qualifier/control metadata 阻止，只读取真实 Makro DOM 证据，不猜单位。

原 dirty worktree 永远不要 stash/reset/clean/overwrite。PR 保持 Draft/unmerged，直到真实商品 coverage、冷/热延迟和 persisted Step 3 acceptance 完成。
