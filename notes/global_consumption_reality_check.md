<!-- SPDX-FileCopyrightText: 2026 Koen van Greevenbroek -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Global Consumption Reality Check (GDD vs FAOSTAT/FBS)

## Context

Validation runs show large food-group imbalances (notably fruits, vegetables, and
starchy vegetables) when supply is anchored to FAOSTAT-like production while
baseline demand is derived from GDD-centered dietary intake.

## Key Finding

This is primarily a **definition mismatch**:

- **FAOSTAT FBS** measures national food **availability/supply** (food entering
  households/retail).
- **GDD** estimates **dietary intake** (what people are estimated to consume).

So FBS-style totals are expected to be higher than GDD-style totals for the same
food category.

## Evidence Used

### External references

- FAO Food Balance Sheets handbook (supply perspective, not individual intake):
  https://www.fao.org/4/X9892e/X9892e01.htm
- FAO Food Balances highlights (large global food-channel totals, including
  fruits/vegetables):
  https://www.fao.org/statistics/highlights-archive/highlights-detail/food-balances-(2010-2023)-new-data-from-fao-supply-utilization-accounts/en
- OECD-FAO Outlook (roots/tubers production and food use in dry-matter terms):
  https://www.oecd-ilibrary.org/sites/70a83e3d-en/index.html?itemId=%2Fcontent%2Fcomponent%2F70a83e3d-en
- GDD methods (survey-based intake modeling):
  https://www.globaldietarydatabase.org/our-data/our-methods/
- GDD-based global intake paper:
  https://www.globaldietarydatabase.org/wp-content/uploads/2024/12/Nature-Medicine-2024-Moran-Global-dietary-intake-and-burdens-of-type-2-diabetes.pdf

### Internal model diagnostics (validation config)

- Baseline demand from GDD-centered pipeline for `starchy_vegetable`:
  ~213 Mt/y
- FAOSTAT FBS item-level food supply for starchy roots (2531-2535):
  ~523 Mt/y
- Gap is ~2.45x, consistent with intake-vs-availability differences.

## Practical Implication for Calibration

For market-clearing in this model:

- Use **FAOSTAT/FBS-like totals** (or a calibrated equivalent) to anchor
  commodity-balance demand.
- Keep **GDD** for intake/health consistency (or as a parallel “intake baseline”).
- If only modeled foods are retained in baseline outputs, redistribute residual
  non-modeled category supply explicitly (projection step) rather than dropping it.
