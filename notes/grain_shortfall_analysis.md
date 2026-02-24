<!-- SPDX-FileCopyrightText: 2025 Koen van Greevenbroek -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Validation Mode: ~500 Mt Grain Food Shortfall Analysis

## Context

Running the model in validation mode (`config/validation.yaml`) with
`use_actual_production: true` and `enforce_baseline_diet: true`, the
solved model shows a ~493 Mt shortfall in refined grain food
consumption relative to baseline diet targets. The shortfall breaks
down as:

| Food         | Target (Mt) | Actual (Mt) | Shortfall (Mt) |
|--------------|-------------|-------------|----------------|
| rice-white   | 387         | 123         | -264           |
| maize (food) | 144         | 0           | -144           |
| flour-white  | 302         | 216         | -86            |
| **Total**    | **833**     | **339**     | **-493**       |

Other food groups also show imbalances (e.g., fruits and vegetables
are overproduced, eggs and poultry underproduced), but the grain
shortfall is by far the largest.

## Mechanism

With `use_actual_production=true`:

- **Crop production** is fixed to observed harvested areas × yields
  (p_nom = p_nom_min = p_nom_max, p_min_pu = 1.0)
- **Animal production** is fixed to FAO levels per country via
  equality constraints at solve time
- **Multi-cropping** is disabled
- **Feed demand is rigid** — the optimizer must allocate crops between
  food and feed

The optimizer sends **77% of grain crops to feed** (vs ~40% in
reality):

- All maize (956 Mt) → ruminant_grain feed
- All wetland rice (328 Mt) → monogastric_grain feed
- 60% of wheat (399 of 666 Mt) → monogastric_grain feed

Total feed demand: **6,388 Mt** (crop-based: 4,008 Mt + grassland:
2,283 Mt + slack: 97 Mt). Real-world total feed is ~4,850–5,250 Mt.

## Primary Cause: Insufficient Grassland (~60% of the problem)

The model has only **1,186 Mha** of pasture vs FAO's **~3,200 Mha**
globally (37%). This forces ruminant animals to get energy from grain
instead of grass, consuming most grain crop production.

Why the pasture undercount:

1. **ESA CCI classification** captures only ~2,000 Mha as grassland
   (classes 110/130/140/150), much less than FAO's definition of
   "permanent meadows and pastures"
2. **"Grazing-only" filtering** (`build_grazing_only_land.py`)
   restricts pasture to land that GAEZ classifies as unsuitable for
   crops — real-world pasture often sits on crop-suitable land
3. **Validation mode** fixes grassland at the observed ESA CCI level
   with no expansion

If grassland were at FAO levels, ~2,740 Mt grain-equivalent would be
freed from ruminant feed — more than enough to close the 493 Mt
shortfall.

## Contributing Factor: Feed Category Classification

Maize is classified as `monogastric_energy` (ME > 15.5 MJ/kg) rather
than `monogastric_grain` (ME 11–15.5). This is nutritionally correct
but creates a practical problem:

- `monogastric_energy` demand: only 49 Mt
- `monogastric_grain` demand: 822 Mt (must come from wheat, rice,
  cassava, millet)

In reality, maize is interchangeable between these categories. The
strict categorization forces all rice and most wheat into monogastric
feed.

## Contributing Factor: Multi-cropping Disabled

Multi-cropping is disabled under `use_actual_production=true`. While
CropGrids harvested area data partially captures multi-cropping
(cropping intensity ~1.02–1.06), the model treats this as physical
area consuming land buses rather than multiple harvests from the same
land.

## Not a Code Bug

This is a data/methodology issue. The optimization logic, feed
conversion calculations, and constraint system all work correctly. The
grassland area data substantially underestimates real-world pasture,
cascading through the model to divert grain from food to feed.

## Feed Balance Summary

```
Total animal feed demand:    6,388 Mt
  grassland:                 2,380 Mt  (incl. 97 Mt slack)
  crop-based:                4,008 Mt
    grain-type:              2,035 Mt  (ruminant_grain 1,213 + monogastric_grain 822)

Total grain crop production: ~2,434 Mt
  going to feed:             1,878 Mt  (77%)
  going to food:               556 Mt  (23%)
  needed for food:           1,049 Mt  (baseline targets)
  food shortfall:              493 Mt

Real-world comparison:
  Grain feed use:            ~1,100 Mt  (~40%)
  Grain food use:            ~1,100 Mt  (~40%)
  Other uses:                  ~600 Mt  (~20%)
```
