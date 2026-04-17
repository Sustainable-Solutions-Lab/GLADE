<!-- SPDX-FileCopyrightText: 2026 Koen van Greevenbroek -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# India Feed Slack Analysis

Analysis of validation model feed slack for India (February 2025).

## Model Feed Balance for India (Mt DM)

Total GLEAM baseline feed demand: **1,449.7 Mt DM** (23% of global 6,263 Mt).

### Demand by product

| Product        | Forage | Roughage | Grain | Protein | Mono grain | Mono LQ | Mono protein | TOTAL   |
|----------------|--------|----------|-------|---------|------------|---------|--------------|---------|
| dairy          | 170.7  | 453.7    | 50.8  | 9.3     | —          | —       | —            | 684.4   |
| dairy-buffalo  | 163.3  | 434.2    | 48.6  | 8.9     | —          | —       | —            | 655.0   |
| meat-sheep     | 14.2   | 47.2     | 2.3   | 0.3     | —          | —       | —            | 64.0    |
| meat-chicken   | —      | —        | —     | —       | 14.7       | 2.2     | 5.1          | 22.0    |
| eggs           | —      | —        | —     | —       | 8.8        | 3.0     | 1.3          | 13.1    |
| meat-cattle    | 0.8    | 2.7      | 5.0   | 0.8     | —          | —       | —            | 9.3     |
| meat-pig       | —      | —        | —     | —       | 0.9        | 0.7     | 0.4          | 1.9     |
| **TOTAL**      | 349.0  | 937.8    | 106.7 | 19.1    | 24.4       | 5.9     | 6.7          | 1449.7  |

Dairy + dairy-buffalo = 92% of total feed.

### Supply balance by category

| Category            | Endogenous | Trade  | Slack  | Total demand |
|---------------------|------------|--------|--------|--------------|
| ruminant_forage     | 99.2       | 0      | +249.8 | 349.0        |
| ruminant_roughage   | 328.2      | +347.4 | +262.1 | 937.8        |
| ruminant_grain      | 65.1       | +41.6  | 0      | 106.7        |
| ruminant_protein    | 15.3       | +3.9   | 0      | 19.1         |
| monogastric_grain   | 5.5        | +18.9  | 0      | 24.4         |
| monogastric_LQ      | 56.3       | -50.4  | 0      | 5.9          |
| monogastric_protein | 6.7        | 0      | 0      | 6.7          |

All slack (512 Mt) is in ruminant forage and roughage.

### Endogenous supply breakdown

**Ruminant forage (99.2 Mt vs 349 Mt demand — 72% gap):**
- Grassland production: 60.3 Mt (7.88 Mha, avg 7.65 t/ha)
- Alfalfa (= all cultivated fodder): 33.9 Mt
- Silage-maize: 4.4 Mt
- Biomass-sorghum: 0.7 Mt

**Ruminant roughage (328.2 Mt vs 938 Mt demand — 65% gap before trade):**
- Rice straw: 127.9 Mt
- Wheat straw: 75.8 Mt
- Exogenous (tree leaves/browse): 81.8 Mt
- Pulse straw: 33.9 Mt (→ monogastric_LQ, not roughage)
- Maize stover: 15.5 Mt
- Sugarcane tops: 14.3 Mt
- Millet stover: 7.4 Mt
- Sorghum stover: 4.5 Mt
- Barley straw: 0.9 Mt

**Total India residue production: 404 Mt DM, 70% to feed (Asia override).**

## Model vs FAOSTAT Product Output

The model produces too much dairy and too little meat/eggs:

| Product        | Model feed (Mt) | Model output (Mt) | FAOSTAT 2018 (Mt) | Ratio |
|----------------|-----------------|--------------------|--------------------|-------|
| dairy          | 684             | 218                | 91                 | 2.4x  |
| dairy-buffalo  | 655             | 209                | 96                 | 2.2x  |
| eggs           | 13              | 2.3                | 5.9                | 0.4x  |
| meat-cattle    | 9               | 0.06               | 4.3                | 0.01x |
| meat-chicken   | 22              | 1.9                | 4.8                | 0.4x  |
| meat-pig       | 2               | 0.1                | 0.4                | 0.3x  |
| meat-sheep     | 64              | 0.3                | 0.8                | 0.4x  |

Model dairy: 427 Mt vs FAOSTAT 187 Mt → **2.3x overproduction**.

Key inconsistency: at Wirsenius S&C Asia FCR of 3.13, 1,339 Mt dairy feed
produces 427 Mt milk; to match 187 Mt FAOSTAT, the herd-level FCR would need
to be 7.2, or the feed would need to be ~585 Mt.

## GLEAM Disaggregation: Root Cause

### How it works

GLEAM baseline is computed in `prepare_gleam_feed_baseline.py`:

1. Start from Mottet et al. (2017) SI Table 2: global feed by OECD/Non-OECD
   region, species, system, and 9 feed types.
2. Non-OECD cattle & buffaloes total: 7,244 Mt DM across 3 systems
   (Grazing 2,321 + Mixed 4,818 + Feedlots 105).
3. **Country share** (Step C): Disaggregate to countries using production shares
   computed at **species level** (all cattle+buffalo products summed in tonnes).
   India gets ~30% of Non-OECD cattle+buffalo feed.
4. **Product share** (Step D): Within each system, split among dairy,
   dairy-buffalo, meat-cattle using FCR-weighted production shares.
5. "Roughage" is decomposed using SI Table 4 (dairy) / SI Table 5 (beef)
   regional composition percentages.
6. Scale from 2010 to 2018 using FAO production growth.
7. Calibrate against GLEAM 3.0 known global total (6.2 Gt DM for 2015).

### The problem: product split, not total

The **total** bovine feed for India (1,349 Mt) is actually close to what a
FCR-consistent calculation gives when INCLUDING beef:

- Dairy: 187 Mt × 3.13 FCR = 585 Mt
- Beef:  4.3 Mt × 153 FCR  = 658 Mt
- Total: 1,243 Mt (model: 1,349 Mt — only 1.08x off)

But the model assigns **1,339 Mt to dairy** and **only 9 Mt to beef**, producing
427 Mt milk (2.3x FAOSTAT) and 0.06 Mt meat (0.01x FAOSTAT). The product split
within the GLEAM disaggregation is putting almost everything into dairy because
India's production by weight is dominated by milk.

### Why the FCR matters

The Wirsenius S&C Asia FCR of 3.13 for dairy already includes worse-than-OECD
efficiency (2x worse than North America). But 3.13 might still be too optimistic
for India specifically:

- 303M bovines produce only 187 Mt milk → herd average 1.7 kg/head/day
- Many animals are non-productive (draught males, sacred cows, dry stock)
- Ratio of total bovines to in-milk animals is ~4:1 (vs ~1.5:1 in N.Am)
- Maintenance feed for non-productive animals must be allocated somewhere

The "correct" herd-level dairy FCR for India might be 5–7, not 3.13. But
India's meat production (4.3 Mt from culled dairy animals) at FCR 153 already
implicitly captures some of this: those animals consumed 658 Mt of feed over
their lives.

### The joint-product problem

In India, cattle meat is mostly a byproduct of dairy — animals are slaughtered
at end of productive life. The model treats dairy and beef as separate feed
streams, but in reality they're joint products of the same animals. The current
disaggregation assigns large amounts of feed to beef production (high FCR) when
that feed was really consumed during the animal's dairy life.

## Indian Government Feed Estimates

### The 510 Mt DM figure (Chand et al. 2014)

This DOES attempt to include all feed sources:
- Crop residues (dry fodder): 320 Mt DM
- Green fodder: 144 Mt DM ← includes production from ALL land types
- Concentrates: 47 Mt DM

The "green fodder" estimate applies crude productivity factors to all land:
- Cultivated fodder: ~40 t fresh/ha × 8.9 Mha = ~364 Mt fresh
- Permanent pastures: ~5 t fresh/ha × 10.3 Mha = ~52 Mt fresh
- Forest grazing: ~1.5 t fresh/ha × 69 Mha = ~104 Mt fresh
- Wasteland, fallow, etc.: ~1 t/ha × various = ~55 Mt fresh
- Total green: ~575 Mt fresh × 0.25 DM = ~144 Mt DM

So it IS a complete accounting attempt, not just cultivated feed. However:
- Productivity assumptions are very crude (1.5 t/ha for ALL forest)
- More recent studies (e.g., DESAGRI 2023) explicitly **exclude grazing intake**
  from demand-side calculations because it's hard to measure
- The 510 Mt might undercount actual grazing, but it's not just cultivated feed
- The higher 651 Mt estimate (epashupalan/ICAR) uses similar methodology

### Demand estimates

- IGFRI: 650–740 Mt DM total demand (ideal feeding levels)
- With 23–32% deficit, actual consumption = 500–570 Mt DM
- The wide range reflects uncertain grazing and informal feed sources

## Grassland Data

- **Model area**: 7.88 Mha (LUIcube managed pasture = total area × grazing
  intensity, where GI = 7–14% for India regions)
- **Government permanent pasture**: 10.3 Mha
- **Satellite grassland (2015)**: 12.3 Mha
- **Culturable wasteland (grazed)**: 12–13 Mha
- **Forest area (significant grazing)**: 71 Mha

LUIcube's "managed pasture" definition is very conservative for India's
extensive, unmanaged grazing systems. Actual area used for grazing is much
larger.

Yields: LUIcube-based, ranging from 2.4 to 16.5 t DM/ha across India regions.

## "Alfalfa" Crop Mapping

"Alfalfa" in the model is a **proxy for all cultivated fodder** crops:
- Maps to GAEZ FDD module (all fodder crops) — sole model crop in that module
- Gets 6.65 Mha GAEZ harvested area in India
- In India context, covers berseem, lucerne, oat fodder, Napier grass, etc.
- BUT: GLEAM South Asia profile assigns 0% to "Legumes & silage", so none of
  the GLEAM forage demand expects to be met by cultivated fodder crops

## Indian Feed System Context

**India's real feed sources** (circa 2018):
- Cultivated fodder area: 8.3–9.1 Mha (stagnant for 25+ years)
- Total crop residue generated: 500–550 Mt fresh (~450–500 Mt DM)
- Green fodder deficit: 11–32% (IGFRI estimates)
- Dry fodder deficit: 23%
- Concentrate deficit: 29%

**Feed composition (DM basis):**
- Dry fodder (crop residues): ~64% of DM
- Green fodder (cultivated + grazing): ~29%
- Concentrates: ~7%

**Livestock** (20th Census, 2019): 193M cattle, 110M buffalo, 149M goats,
74M sheep = 535M total.

## Bug: Missing Buffalo Meat in FAOSTAT Mapping

**Root cause of the product split issue.** The `faostat_items` config maps
`meat-cattle` to only "Meat of cattle with the bone, fresh or chilled". But
India stopped reporting cattle meat in FAOSTAT after 1990 — all bovine meat is
reported as **"Meat of buffalo, fresh or chilled"** (3.1 Mt in 2010). This
item is NOT mapped, so India's meat-cattle production = 0 in the
disaggregation.

### Effect on product shares

In `compute_product_shares` for India's Mixed/Grazing systems:
- dairy: 59.5 Mt × 23.3 MJ/kg = 1,387 → share 48.8%
- dairy-buffalo: 62.4 Mt × 23.3 MJ/kg = 1,453 → share 51.2%
- meat-cattle: **0** × 848.3 MJ/kg = **0** → share **0.0%**

Only the Feedlots system (single-product, meat-cattle only) gives India any
beef feed: 9.3 Mt DM (0.7% of India's 1,349 Mt bovine total).

### With buffalo meat included

Adding India's 3.1 Mt of buffalo meat at FCR 848 MJ/kg:
- meat-cattle weighted = 3.1M × 848 = 2,650 → share **48.3%**
- dairy share drops from 48.8% → 25.3%
- dairy-buffalo from 51.2% → 26.5%

This would roughly halve the dairy feed allocation.

### Other affected countries

- **Nepal**: 162k t buffalo meat missing (same zero-cattle-meat pattern)
- **Pakistan**: 830k t buffalo meat not counted
- **China**: 629k t, **Egypt**: 398k t, and ~15 others

### Minimal fix

Add "Meat of buffalo, fresh or chilled" to `faostat_items["meat-cattle"]` in
`config/default.yaml`. This mirrors the proxy pattern already used (goat→sheep,
duck/turkey→chicken).

## Proposed Alternative: Bottom-Up Feed Scaling

Rather than just patching the buffalo meat bug, a more robust approach would be
to scale baseline feed intakes at the country × product level to match FAOSTAT
production × assumed FCR. This would:

1. Start from the GLEAM top-down disaggregation (for feed composition / category
   splits)
2. Compute expected total feed per country × product: FAOSTAT_production × FCR
3. Scale the GLEAM-derived feed to match this expected total

This bottom-up calibration would fix the India product split AND correct
imbalances in other countries where GLEAM's production-share disaggregation
over- or under-allocates feed. The feed category composition (forage/roughage/
grain/protein splits) would still come from GLEAM's regional composition tables.

## Summary of Issues

### Issue 1: Product split bug (buffalo meat missing from FAOSTAT mapping)

Most impactful. See "Bug" section above. Causes 2.3x dairy overproduction and
0.01x beef underproduction for India. Easy to fix, but a broader bottom-up
scaling approach would be more robust.

### Issue 2: Feed supply gaps (independent of demand)

Even with correct demand, the supply side has gaps:
- **Grassland area** (7.9 Mha) is too small vs actual grazing area (10–25+ Mha)
- **No forage trade** means spatial mismatches can't be resolved
- **Residue production** (404 Mt) is close to reality but may miss some crops
- **Roughage trade** (347 Mt imports) is unrealistic for India

### Issue 3: Wirsenius FCR may be too optimistic for India

S&C Asia regional average of 3.13 for dairy might not capture India's extreme
non-productive herd ratio (4:1 bovines to in-milk animals).

### Issue 4: Joint-product modeling of dairy and beef

India's beef is a byproduct of dairy. The model separates them, causing
allocation confusion. This is a fundamental model design consideration.
