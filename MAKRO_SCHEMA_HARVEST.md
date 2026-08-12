# Makro full-vertical schema harvest

This utility inventories Makro Marketplace product attributes from the official
Vertical-specific **Bulk Product Creation** Excel loadsheets. It is deliberately
separate from the listing creation pipeline.

## Why this source

Makro's Seller Help Centre documents the Bulk Product Creation contract:

- choose a Vertical and download the Excel loadsheet specific to that Vertical;
- row 1 = field heading;
- row 2 = field format;
- row 3 = example;
- row 4 = description;
- blue headers = mandatory;
- green headers = optional;
- Single Text / Multi Text fields linked to allowed values must use the predefined
  values in the Index tab.

Official guide:
`https://makromarketplace.helpcentre.app/article/iq8ee6b-how-to-create-products-in-bulk-step-by-step-guide`

Makro also documents that Vertical-specific predefined attributes determine
variant capabilities:
`https://makromarketplace.helpcentre.app/article/ip3of8a-how-to-add-variants-of-product-listings`

## Safety boundary

`makro_harvest_schema.py` is read-only with respect to listings:

- requires the existing authenticated Makro Edge/CDP session;
- refuses to auto-start/restart Edge;
- opens a fresh seller-portal tab instead of navigating an existing listing tab;
- navigates only through Listings -> Bulk Product Creation -> Create Product;
- selects Vertical values and clicks Download;
- never uploads a loadsheet;
- never creates/saves a product listing;
- never sends anything to QC;
- closes only the tab it created.

## Pass 1: discover all Verticals

```powershell
python makro_harvest_schema.py --discover-only
```

Expected artifact:

`logs/makro-schema-harvest/harvest-<timestamp>/harvest-report.json`

The report contains `discovered_vertical_count` and the exact Vertical labels.
If the portal DOM cannot be resolved uniquely, the run fails closed and writes a
full-page screenshot + HTML diagnostic instead of guessing.

## Pass 2: download + build registry

```powershell
python makro_harvest_schema.py
```

Artifacts:

- `downloads/<vertical>/<original Makro filename>.xlsx`
- `makro-schema-registry.json`
- `harvest-report.json`

The original suggested filename is preserved inside a per-Vertical directory.
Nothing is uploaded back to Makro.

## Offline parser only

Existing downloaded loadsheets can be parsed without opening the browser:

```powershell
python makro_harvest_schema.py --parse-only "C:\path\to\downloads"
```

## Registry contract

For every Vertical the registry preserves each field's:

- normalized attribute key;
- exact Makro label;
- raw row-2 format text;
- normalized field type;
- row-3 example;
- row-4 description;
- required / optional / unknown requirement state;
- allowed values when recoverable from hyperlink, Excel validation, or Index tab;
- raw header fill and hyperlink reference;
- source workbook and column position.

It also builds a global `field_catalog` showing where an attribute appears across
Verticals, which types it uses, its requirement distribution, and the union of
allowed values.

## Development acceptance

Before using the registry in production field execution, collect the full Seller
Portal inventory and review:

1. discovered Vertical count vs downloaded Vertical count;
2. download failures;
3. parse failures;
4. `field_types` distribution;
5. any `unknown` field types;
6. any `unknown_requirement` fields;
7. attributes whose type changes across Verticals;
8. attributes with allowed-value links but no recovered allowed values.

Only after that inventory is complete should the production Field Engine be
expanded from observed Makro field families. The registry must augment current
live-DOM validation, not replace it: the current live schema remains the final
source of truth for the controls that are actually rendered at execution time.
