# Image Selection Lab

This benchmark isolates supplier Product Photos selection from 1688 capture, Makro login, category selection, field filling, upload and save. A case is only:

`canonical target product + local candidate images + human ground truth`.

The first strategy, `current-v6`, reuses the production `app.listing_image_ranker` Ownership and Gallery request builders and parsers. The Lab does not maintain a second copy of production prompts.

`qwen-vl-rerank-v1` is registered as an experiment slot but intentionally refuses to run until a labelled `current-v6` baseline exists. This prevents a new production architecture from being adopted without evidence.

## Local-only data

`cases/`, `cache/` and `runs/` are ignored by git. Real supplier images and imported production failures therefore stay on the local test machine by default.

A case lives at:

```text
evals/image_selection/cases/<case_id>/
  case.json
  images/
    img_001.jpg
    img_002.jpg
```

`case.json` schema:

```json
{
  "schema_version": 1,
  "case_id": "nivea_deodorant_001",
  "suite": ["regression", "hard_negative"],
  "target_product": {
    "product_type": "roll-on deodorant",
    "brand": "Nivea",
    "model": "Deep Espresso",
    "size": "50 ml",
    "pack_count": 1,
    "variant": "black bottle"
  },
  "candidates": [
    {
      "image_id": "img_001",
      "file": "images/img_001.jpg",
      "ground_truth": {
        "ownership": "EXACT_TARGET",
        "auto_upload_allowed": true,
        "relevance_grade": 3,
        "main_image_allowed": true
      }
    }
  ]
}
```

Allowed ownership labels are exactly the production semantic classes:

- `EXACT_TARGET`
- `TARGET_PACKAGING_OR_DETAIL`
- `SAME_PRODUCT_OTHER_VARIANT`
- `OTHER_PRODUCT`
- `PAGE_ASSET`
- `UNCERTAIN`

`auto_upload_allowed` must be true only for `EXACT_TARGET` and `TARGET_PACKAGING_OR_DETAIL`.

`relevance_grade` is 0..3: 3 = strongest main image, 2 = useful support image, 1 = correct but weak/redundant, 0 = forbidden.

## Import a real failure

A production `listing-image-selection.json` can be converted into a local case without revisiting the supplier page:

```powershell
python -m tools.image_selection_lab import-case `
  --selection-json "D:\path\listing-image-selection.json" `
  --case-id real_failure_001
```

The importer copies candidate images and target identity. Existing AI predictions are stored only as `previous_prediction`. `ground_truth` is deliberately left null, so the imported case cannot run until a human labels every image.

## Provider environment

The Lab uses the repository's existing semantic provider configuration and never stores an API key in cases, cache or reports.

Example PowerShell environment:

```powershell
$env:AI_PROVIDER = "openai-compatible"
$env:AI_MODEL = "<the same multimodal model you want to evaluate>"
$env:AI_BASE_URL = "<your OpenAI-compatible API root>"
$env:AI_API_KEY = "<key>"
```

The default API key variable for `openai-compatible` is `AI_API_KEY`. You can override it with `--api-key-env`.

## First live baseline

Run one case:

```powershell
python -m tools.image_selection_lab run `
  --case real_failure_001 `
  --strategy current-v6 `
  --mode live `
  --model "$env:AI_MODEL" `
  --base-url "$env:AI_BASE_URL"
```

Run all labelled cases:

```powershell
python -m tools.image_selection_lab run `
  --suite all `
  --strategy current-v6 `
  --mode live `
  --model "$env:AI_MODEL" `
  --base-url "$env:AI_BASE_URL"
```

The command prints `report=<path>`. Open that `report.html` locally for the contact sheet and per-image decisions.

## Replay without spending AI quota

A semantic response cache is keyed by strategy, safe provider/model configuration, the production request contract, canonical target product and image SHA-256 values. Local paths and API keys are not part of the persisted cache payload.

After one live run, the same semantic inputs can be replayed without any model network request:

```powershell
python -m tools.image_selection_lab run `
  --suite all `
  --strategy current-v6 `
  --mode replay `
  --model "$env:AI_MODEL" `
  --base-url "$env:AI_BASE_URL"
```

A replay cache miss is a hard error. Replay mode never falls through to live AI.

Use `--refresh-cache` only in live mode when the exact same inputs should intentionally be called again.

## Order-bias test

```powershell
python -m tools.image_selection_lab run `
  --suite all `
  --strategy current-v6 `
  --mode live `
  --repeat 3 `
  --shuffle `
  --seed 17 `
  --model "$env:AI_MODEL" `
  --base-url "$env:AI_BASE_URL"
```

The report includes selection Jaccard stability and top-1 stability across repeats.

## Safety gates and metrics

The primary gates are intentionally precision-first:

- selecting `OTHER_PRODUCT`, `PAGE_ASSET` or `UNCERTAIN` is a wrong-product leak;
- selecting `SAME_PRODUCT_OTHER_VARIANT` is a variant leak;
- any ground-truth-disallowed selected image makes the gallery unsafe;
- an all-negative case must return an empty gallery.

The aggregate report also records `main_image_hit_at_1`, micro `precision@5`, micro `recall@5`, mean `NDCG@5`, semantic calls, actual network calls, cache hits, token usage and latency.

No hardcoded model pricing is stored because provider pricing changes. To estimate CNY cost for a run, pass the current prices explicitly:

```text
--input-price-per-million <CNY> --output-price-per-million <CNY>
```

## Benchmark v1: frozen 12-case design

The first benchmark is now defined in `evals/image_selection/benchmark_v1.json`. It is a test specification, not a set of committed product images. Real images stay under the gitignored `cases/` directory.

The twelve required failure modes are:

1. obvious unrelated page pollution;
2. same category but different brand;
3. same brand but different product line;
4. exact model but wrong colour;
5. exact family but wrong size/capacity;
6. wrong pack count;
7. bundle/kit versus standalone configuration;
8. included-component detail versus unrelated accessory;
9. packaging/detail images mixed with near-duplicates;
10. visual-only images with little or no readable text;
11. text-critical near-identical SKU variants;
12. a 32-candidate all-negative stress case with zero valid target images.

Use real supplier-page captures wherever possible. Do not clean the candidate pool to make the model look better, and do not use generated or stock images in the baseline suite. Each imported case must be labelled by a human; previous AI output is diagnostic history only.

The hard suite gate is intentionally strict: zero wrong-product leaks, zero variant leaks, 100% safe galleries, and 100% empty output on all-negative cases. Quality targets such as main-image hit rate, recall and NDCG are secondary and cannot compensate for a safety regression.

Execution order is fixed: run `current-v6` once on the frozen labelled cases, inspect/replay for free, then run the shuffled order-bias subset, and only after that implement `qwen-vl-rerank-v1` and compare both strategies on the exact same frozen cases.

A new strategy may not replace production if it introduces any wrong-product, wrong-variant or all-negative failure that the current baseline did not have, even if it is cheaper or has better ranking metrics.
