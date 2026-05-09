# Data Packaging

Use `assets/data/` for stable machine-readable business files that the scripts consume directly.

## Put in `assets/data/`

1. `knowledge-base.xlsx`
   This is operational input data for deterministic lookup.
2. `classification-table.xlsx`
   This is benchmark data and a reusable field-definition source.

## Do not put these files in `references/`

`references/` is for text material that the model may read into context.
Large `.xlsx` files are poor reference material for context loading and should stay as bundled assets.

## Recommended pattern

1. Keep the binary or tabular source file in `assets/data/`
2. Keep a short schema or maintenance note in `references/`
3. Let scripts default to the bundled asset path, while still allowing overrides
