# food-opt data bundle {{VERSION}}

Processed-data companion archive for the
[food-opt](https://github.com/koen-vg/food-opt) global food systems
optimization model. Bundles a small set of derivative artifacts so the
full workflow can run without hand-downloading restricted-license source
data and without registering for a Copernicus API key.

- **Bundle version:** `{{VERSION}}`
- **Build date:** `{{BUILD_DATE}}`
- **food-opt commit:** `{{FOOD_OPT_COMMIT}}`
- **Baseline year:** `{{BASELINE_YEAR}}`
- **Zenodo DOI:** `{{ZENODO_DOI}}`
- **License:** `CC BY-NC 4.0` (see `LICENSE`)

## What's inside

{{FILE_TABLE}}

Per-file source attributions and any provider-specific terms are listed
in `ATTRIBUTIONS.md`. SHA-256 hashes for every file are recorded in
`manifest.yaml`.

## Compatibility

This bundle is only valid for food-opt configurations satisfying:

- `baseline_year == {{BASELINE_YEAR}}`
- `countries` ⊆ the {{COUNTRIES_COUNT}} ISO3 codes covered by the bundle
- `food_groups.included` ⊆ `{{FOOD_GROUPS_LIST}}`
- `health.causes` ⊆ `{{CAUSES_LIST}}`
- `health.risk_factors` ⊆ `{{RISK_FACTORS_LIST}}`

food-opt's startup validation enforces these constraints when
`data_bundle.enabled: true`.

## Using the bundle

Inside a food-opt checkout:

```yaml
# config/your_config.yaml
data_bundle:
  enabled: true
  version: "{{VERSION}}"
  zenodo_doi: "{{ZENODO_DOI}}"
```

Then run any food-opt target as usual. The workflow downloads this
archive, verifies hashes against `manifest.yaml`, and routes around the
local prep pipelines for the bundled files.

## Regenerating the bundle from scratch

The bundle is reproducible from a food-opt checkout that has access to
the original (manually-downloaded) source datasets. From the food-opt
project root:

```bash
tools/build-data-bundle
```

This produces `bundle/{{VERSION}}/food-opt-data-bundle-{{VERSION}}.zip`.

## Citation

See `CITATION.cff`. When using results that depend on this bundle,
please cite the original data providers in addition to the bundle:
the IHME GBD source identifier required under their license is
quoted verbatim in `ATTRIBUTIONS.md`.
