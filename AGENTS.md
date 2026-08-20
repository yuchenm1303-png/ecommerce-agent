# AGENTS.md

## 唯一生产链

`Makro live schema`
→ `一个 1688/供应商商品链接`
→ `可选：本次销售规格 / 颜色 / 套装 offer intent`
→ `独立 source Edge 自动采集完整当前页`
→ `AI 直接填写 Makro live fields`
→ `Web 只补仍为空的字段`
→ `Content Policy + Thin Hard Guards`
→ `只读 Fill Plan`
→ `makro_execute_listing.py`
→ `Save/reopen verification`
→ `Product Photos persistence`

`Send to QC` 绝对禁止自动点击。

核心原则：**商品链接是新商品唯一必填的人工产品输入。若同一供应商页包含多个颜色/规格/套装，GUI 可以额外接收一个可选的“本次销售规格 / 套装”作为 listing offer intent，用来告诉 AI 这一次到底卖哪个 supplier-supported variant/bundle；它不是 Makro Seller SKU，也不是新的 Product Profile。页面写什么就采什么；AI负责理解；空字段才搜索；搜不到不循环硬找、不编造。optional 字段可以继续留空。不要增加 Final Resolve、Python 商品语义规则或循环复核。**

## 商品输入 / Listing Offer Intent

新商品不再要求人工 Makro SKU、客户 QA 里的旧 SKU 编号、expected model/brand、product-table、facts-json、旧 snapshot 或半成品答案。

Resolver 的产品身份锚点仍然是 exact `--product-url`。页面自己的 `skuId`、规格文字、variant data 仍只是原始页面证据，不由 Python 解释。

可选 listing offer intent 只描述**本次实际售卖范围**，例如 `黑色净化器 + 2瓶香薰精油`。它可以帮助 AI 在页面已经存在的多 SKU、多颜色、多 pack/bundle 中消歧，并影响 Sales Package、Model Name/标题文案和本次商品图优先顺序；它不能凭空创造页面/证据没有的规格，也不能覆盖真实冲突。Single 每次 run 独立冻结；Batch 每个 Job 独立传递，禁止跨 Job 泄漏。

## Source Capture

`app/source_capture.py` / `app/source_snapshot.py` 只做机械采集：独立 source Edge/CDP 9333、rendered text、table/dl rows、JSON-LD、bounded DOM/inline-script variant/SKU/spec/offer 原始片段和 full-page screenshot。

禁止自动选择 SKU/款式、禁止 Python 判断 variant、禁止绕过 CAPTCHA/风控。需要合法人工验证时停止并等待用户完成。offer intent 不会驱动 Python 去点击 supplier SKU；它只作为后续 AI 的显式 seller scope。

## AI 直接填字段

`app/field_mapping.py` / 当前 product-fact pipeline 接收原始证据 + 当前 Makro fields；存在 listing offer intent 时同时接收该 seller scope。

AI 负责跨语言理解、页面规格/variant 关系、packaging/body/mount scope、cabin/rear、manual/UI language、字段语义映射、冲突判断和文案生成。

正常状态：`READY / CONFLICT / MISSING`。Local READY / CONFLICT 冻结，Web 不重新搜索或推翻。

机械 batch 只能按 live schema 顺序和固定 batch size 切片。禁止建立 camera/storage/dimension、颜色/SKU 套装等 Python 商品分类表。

## Content Policy

`app/listing_content_policy.py` 是 seller-facing 字段表达/证据严格度规则，不是第二个商品语义层。

- Model Name、Description、Keywords 等文案字段可以基于已解析事实和 offer intent 做 grounded synthesis；
- Sales Package 表示**本次买家实际收到的物品清单**，有 offer intent 时优先按该销售套装消歧，再与 exact supplier evidence 协调；不得按品类常识补“标准配件”；
- EAN/GTIN、Certifications 等 exact/compliance 字段禁止 best-effort 编造；
- Model Name、Sales Package、EAN/Certification 以及 live schema 明确标记为标题组成属性的关键 required 字段，禁止使用 `N/A` / `1` / 随机 option 作为通用兜底；仍未解决时必须让用户确认；
- 其他普通 unresolved required 字段保留现有 deterministic non-AI fallback + live hard guard 行为；
- 标准规格标点如 `USB-C`、`2-in-1`、`220-240 V` 可以保留；禁止无意义装饰符号和不受证据支持的医疗/营销声明。

## Web Fill

`app/web_enrichment.py` 只处理 Local 后仍为 `MISSING / REVIEW` 的非经营字段。

Web 使用 exact `source_product_url`、已确定 local READY/CONFLICT 商品指纹和本批 unresolved fields。一次调用直接完成搜索和字段答案，没有 Final Resolve。

returned URL 必须来自实际 `web_search` source；模型编造 URL 丢弃。Web/comparable 信息可以成为后续 policy-aware 文案的上下文，但不能把相似商品的 EAN、认证或包装清单直接当成本商品事实。

## Makro Seller SKU / required 用户补充

Makro SKU ID 是 seller-controlled identifier，不是 supplier SKU、不是 listing offer intent，也不是产品事实。

`app/business_fields.py` 为每次 listing attempt 机械生成 fresh 12 位数字 Seller SKU；同一 run 内复用同一个值，避免重试阶段漂移；它不进入 AI 商品身份，也不拿去搜索或验证商品。

其他 business fields（价格、库存、MOQ、Fulfilment、Shipping SLA、Listing Status、Selling Region 等）只能来自明确 seller/business/config/rule 输入；缺失就 blocked，不允许 AI/Web 猜。

当前 live schema 已明确知道哪些字段 `required=true`。正常 Resolver/Web/content policy 跑完后：关键 protected required 字段必须由用户显式确认；普通 required 字段可以继续走现有 deterministic non-AI fallback。所有用户值/兜底值都只经过当前 Makro option/unit/field hard guards，不得触发第二轮 AI/Web 搜索。

## Python 只能守机械边界

允许：source capture、batch/cache、显式 offer intent 传递/隔离、live schema/source rebind、citation provenance、generated seller SKU、business lock、option/qualifier/multi-value shape、GTIN/numeric/maxlength、价格/MOQ关系、DOM唯一定位、React readback、Save/reopen、基于已有 image observations 的稳定图片排序、Product Photos persistence、禁止 Send to QC。

禁止 Python 判断：网页是不是同款、某个 supplier SKU 代表什么、某种颜色/套装是什么意思、manual language 是否等于 UI language、front+cabin 是否等于 rear、尺寸属于包装还是机身、缺失功能是否等于 No。offer intent 的语义解释仍由 AI 完成。

## Planner / Browser

`makro_plan_listing.py --scan-live-schema`：只读扫描。

最终 planner 必须用 Resolver 的同一 product URL、同一 source snapshot/screenshot 重建 grounding，并 strict rebind `ai-decisions.json`。

新生产 Browser 入口是 `makro_execute_listing.py`。它不读 QA、不接受人工 `--sku`，只使用同一 product URL、source evidence、decision packet 和 live schema。它复用成熟浏览器能力，但不重新解释商品。

`makro_preview_listing.py` 仅保留底层成熟浏览器 helper/旧兼容 CLI，不再作为新商品生产入口。

**Fill Plan 的 READY 是最终写入许可。** 生产 executor 只保留“当前 live field 必须唯一匹配”这一写错位置防线；不得再用“当前控件看起来已有值”之类的二次判断把 READY 静默跳过。protected required 若未确认，必须在真实写入前 fail closed。

`input[type=file].files > 0` 不等于图片上传成功。Product Photos 每次执行必须先读取当前 live completion/capacity，再把明确的 `--upload-image` 限定到当前真实剩余槽位。对**本次实际进入事务的图片子集**仍必须逐张确认 N/N staged，且一次 Save 后验证 completion count；上传、Save、持久化验证失败一律 fail closed。若用户明确请求的图片数量超过 live gallery 剩余容量，不允许把容量不足伪装成上传失败，也不允许删除/覆盖已有图片腾位置：只上传当前可容纳子集，剩余路径必须以 `request_status=capacity_limited` / `skipped_no_capacity`、`omitted_due_capacity` 和 warning 明确记录。只要最终 gallery 已有至少 1 张持久化图片，纯容量饱和不应把已成功持久化的 Step 3 草稿判成整单 FAILED。offer intent 只允许调整候选图片顺序，不能绕过上述事务和持久化校验。

schema/source drift fail closed；真实 section Save 后保持现有 reopen persisted verification。

## 工作树安全

原始 dirty worktree不允许 reset/stash/clean/checkout 覆盖。开发、同步和验收使用独立 preview worktree。

不要由底层 workflow subprocess 自动启动/重启/关闭长期 Makro Edge；CDP 消失就停止并保留现场。正式 GUI 的 browser ownership wrapper 负责 GUI 自己的 dedicated session 生命周期。

## 开发验收

正式修改后至少要求 `pytest -q`、GitHub Actions tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品先跑：

`URL + optional offer intent → capture → cold Resolver → hot Resolver → read-only Fill Plan`

在 read-only 结果通过前，不进入真实 Makro persistence。