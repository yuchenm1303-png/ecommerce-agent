# AGENTS.md

## 唯一生产链

`Makro live schema`
→ `一个 1688/供应商商品链接`
→ `独立 source Edge 自动采集完整当前页`
→ `AI 直接填写 Makro live fields`
→ `Web 只补仍为空的字段`
→ `Thin Hard Guards`
→ `只读 Fill Plan`
→ `makro_execute_listing.py`
→ `Save/reopen verification`
→ `Product Photos persistence`

`Send to QC` 绝对禁止自动点击。

核心原则：**链接是新商品唯一人工产品输入。页面写什么就采什么；AI负责理解；空字段才搜索；搜不到留空；真实冲突保留。不要增加 Product Profile、Final Resolve、Python 商品语义规则或循环复核。**

## 商品输入

新商品不再要求人工 Makro SKU、客户 QA 里的旧 SKU 编号、expected model/brand、product-table、facts-json、旧 snapshot 或半成品答案。

Resolver 的产品身份锚点是 exact `--product-url`。页面自己的 `skuId`、规格文字、variant data 只作为原始页面证据，不是用户输入，也不由 Python 解释。

## Source Capture

`app/source_capture.py` / `app/source_snapshot.py` 只做机械采集：独立 source Edge/CDP 9333、rendered text、table/dl rows、JSON-LD、bounded DOM/inline-script variant/SKU/spec/offer 原始片段和 full-page screenshot。

禁止自动选择 SKU/款式、禁止 Python 判断 variant、禁止绕过 CAPTCHA/风控。需要合法人工验证时停止并等待用户完成。

## AI 直接填字段

`app/field_mapping.py` 直接接收原始证据 + 当前 Makro fields。

AI 负责跨语言理解、页面规格/variant 关系、packaging/body/mount scope、cabin/rear、manual/UI language、字段语义映射、冲突判断和文案生成。

正常状态：`READY / CONFLICT / MISSING`。Local READY / CONFLICT 冻结，Web 不重新搜索或推翻。

机械 batch 只能按 live schema 顺序和固定 batch size 切片。禁止建立 camera/storage/dimension 等 Python 商品分类表。

## Web Fill

`app/web_enrichment.py` 只处理 Local 后仍为 `MISSING / REVIEW` 的非经营字段。

Web 使用 exact `source_product_url`、已确定 local READY/CONFLICT 商品指纹和本批 unresolved fields。一次调用直接完成搜索和字段答案，没有 Final Resolve。

returned URL 必须来自实际 `web_search` source；模型编造 URL 丢弃。

## Makro SKU

SKU ID 是 seller-controlled identifier，不是产品事实。

`app/business_fields.py` 根据 exact product URL 机械生成稳定 12 位数字 SKU：相同商品 URL 稳定复用，query/tracking 参数不影响结果，source_type=`rule`，不进入 AI 商品身份，也不拿去搜索或验证商品。

其他 business fields（价格、库存、MOQ、Fulfilment、Shipping SLA、Listing Status、Selling Region 等）只能来自明确 seller/business/config/rule 输入；缺失就 blocked，不允许 AI/Web 猜。

## Python 只能守机械边界

允许：source capture、batch/cache、live schema/source rebind、citation provenance、generated seller SKU、business lock、option/qualifier/multi-value shape、GTIN/numeric/maxlength、价格/MOQ关系、DOM唯一定位、React readback、Save/reopen、Product Photos persistence、禁止 Send to QC。

禁止 Python 判断：网页是不是同款、某个 SKU 代表什么、manual language 是否等于 UI language、front+cabin 是否等于 rear、尺寸属于包装还是机身、缺失功能是否等于 No。

## Planner / Browser

`makro_plan_listing.py --scan-live-schema`：只读扫描。

最终 planner 必须用 Resolver 的同一 product URL、同一 source snapshot/screenshot 重建 grounding，并 strict rebind `ai-decisions.json`。

新生产 Browser 入口是 `makro_execute_listing.py`。它不读 QA、不接受人工 `--sku`，只使用同一 product URL、source evidence、decision packet 和 live schema。它复用旧执行 helpers，但不重新解释商品。

`makro_preview_listing.py` 仅保留底层成熟浏览器 helper/旧兼容 CLI，不再作为新商品生产入口。

已有非-placeholder 用户值不覆盖；schema/source drift fail closed；真实 section Save 后必须 reopen 验证 persisted values。

`input[type=file].files > 0` 不等于图片上传成功。Product Photos 必须看到 Makro 接受信号，Save 后还要验证 completion count。

## 工作树安全

原始 dirty worktree不允许 reset/stash/clean/checkout 覆盖。开发、同步和验收使用独立 preview worktree。

不要自动启动/重启长期 Makro Edge；CDP 消失就停止。不要关闭 browser profile。

## 开发验收

正式修改后至少要求 `pytest -q`、GitHub Actions tests、mock-e2e、browser automation dry-run、browser probe 全部通过。

真实商品先跑：

`URL capture → cold Resolver → hot Resolver → read-only Fill Plan`

在 read-only 结果通过前，不进入真实 Makro persistence。
