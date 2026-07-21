"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Stage 1 of the multi-cropping baseline: derive an observed baseline area for
crop-sequence combinations from MIRCA-OS 2020, on the global 5-arcmin grid.

This is a config-independent Snakemake checkpoint (shared across configs). It
writes to its output directory:

  * ``combinations.yaml`` -- the discovered combination set (crop sequences and
    water supplies in GLADE ``i``/``r`` codes), merged over
    ``config["multiple_cropping"]`` to form the effective combination set.
  * ``baseline/{combination}_{ws}.tif`` -- per-combination, per-water-supply
    sparse 5-arcmin GeoTIFF of the *physical link area* ``A`` (ha): the field area
    that runs the whole sequence once (an ``n``-cycle combination on ``A`` ha
    harvests ``n * A`` ha).
  * ``residual_multicrop.tif`` -- extra-cycle harvested area not attributed to any
    combination, left for the bulk land-correction generator.

Method, per cell:

1. Magnitude ``M_total = sum_crops (harvested_ir + harvested_rf) - footprint``,
   clipped at 0, where ``footprint = footprint_ir + footprint_rf`` are MIRCA's
   AEI-capped maximum-monthly-cropped-area layers (no ``tot`` layer ships, and the
   two systems occupy disjoint land, so the sum is single-count). ``M_total`` is
   the harvested area above the physical field footprint -- the extra-cycle area.
2. Candidate combinations are gated on **MIRCA observation** (both crops harvested
   in the given system) plus the GAEZ multiple-cropping-zone cycle-count limit.
   ``sequence_feasible`` on GAEZ windows is deliberately NOT used as the gate: GAEZ
   attainable season lengths overshoot the farmed cycle, so it rejects ~all
   observed irrigated double-cropping. GAEZ timing enters only later, at the
   Stage-2 water split.
3. Repeated same-crop cycles (double/triple rice) use the MIRCA subcrop stack
   (``Rice1/2/3``) for mutually consistent physical supports, not ``min(area,
   area)``.
4. Each candidate has physical cap ``A_max`` and extra-cycle capacity
   ``(n-1) * A_max``. The cell's ``M_total`` is allocated across candidates
   proportionally to capacity, never exceeding it; any unallocated remainder is
   residual. The physical link area written out is ``A = E / (n-1)``.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
import yaml

from workflow.scripts.build_multi_cropping import WETLAND_RICE_CROPS, ZONE_CAPABILITIES


def load_tif(path: str) -> np.ndarray:
    """Load a GeoTIFF band as float64 with negatives (nodata sentinels) zeroed."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float64)
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    return arr


def load_subcrop_maxmonth(path: str) -> np.ndarray:
    """Collapse a MIRCA monthly growing-area NetCDF to its max over months (ha)."""
    da = xr.open_dataset(path)["harvested_area"]
    arr = np.nan_to_num(da.values, nan=0.0)  # (month, lat, lon)
    arr[arr < 0] = 0.0
    return arr.max(axis=0)


def zone_mask(zone_arr: np.ndarray, n_cycles: int, n_rice: int) -> np.ndarray:
    """Cells whose GAEZ multiple-cropping zone permits ``n_cycles`` (``n_rice`` rice)."""
    allowed = [
        code
        for code, cap in ZONE_CAPABILITIES.items()
        if cap.get("valid", False)
        and int(cap.get("max_cycles", 0)) >= n_cycles
        and int(cap.get("max_wetland_rice", 0)) >= n_rice
    ]
    return np.isin(zone_arr, allowed)


def candidate_capacity(
    crops: list[str],
    ws: str,
    crop_area: dict[tuple[str, str], np.ndarray],
    zone_arr: np.ndarray,
    rice_support: dict[str, np.ndarray],
) -> np.ndarray:
    """Physical cap ``A_max`` (ha) for a crop sequence in one water supply, per cell.

    Distinct-crop sequences: ``A_max = min_k area_{crop_k, ws}`` where both crops
    are observed, under the zone mask. Repeated wetland-rice sequences: disjoint
    supports from the MIRCA subcrop stack (double = cells with a 2nd but not 3rd
    rice cycle; triple = cells with a 3rd cycle).
    """
    n = len(crops)
    n_rice = sum(1 for c in crops if c in WETLAND_RICE_CROPS)
    zmask = zone_mask(zone_arr, n, n_rice)

    repeated_rice = n_rice == n and n >= 2  # all cycles are wetland rice
    if repeated_rice:
        rice2 = rice_support[ws]  # area with a 2nd rice cycle (>= double)
        rice3 = rice_support[ws + "3"]  # area with a 3rd rice cycle (triple)
        if n == 2:
            a_max = np.clip(rice2 - rice3, 0.0, None)  # exactly-double fields
        elif n == 3:
            a_max = rice3.copy()
        else:
            raise ValueError(f"Unsupported repeated-rice cycle count: {n}")
        return np.where(zmask, a_max, 0.0)

    # Distinct-crop sequence: min over the (possibly repeated) crop areas.
    stack = np.stack([crop_area[(c, ws)] for c in crops], axis=0)
    both_observed = np.all(stack > 0, axis=0)
    a_max = np.min(stack, axis=0)
    return np.where(zmask & both_observed, a_max, 0.0)


def allocate(
    m_total: np.ndarray,
    capacities: list[np.ndarray],
    cycle_counts: list[int],
) -> tuple[list[np.ndarray], np.ndarray]:
    """Allocate the extra-cycle magnitude across candidates, capped by capacity.

    Each candidate ``i`` has extra-cycle capacity ``(n_i - 1) * A_max_i``. Per
    cell: if total capacity fits within ``m_total`` every candidate is filled and
    the remainder is residual; otherwise ``m_total`` is rationed proportionally to
    capacity (no candidate exceeds its cap, and the sum never exceeds ``m_total``,
    so no co-located rotation is invented). Returns per-candidate *physical link
    area* ``A = E / (n-1)`` and the residual extra-cycle area.
    """
    extra_caps = [
        (n - 1) * cap for n, cap in zip(cycle_counts, capacities)
    ]  # capacity in extra-cycle units
    total_cap = np.sum(extra_caps, axis=0)
    # scale in [0,1]: 1 where capacity fits, else m_total/total_cap
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(total_cap > m_total, m_total / total_cap, 1.0)
    scale = np.clip(np.nan_to_num(scale, nan=0.0, posinf=0.0), 0.0, 1.0)

    areas: list[np.ndarray] = []
    allocated_extra = np.zeros_like(m_total)
    for n, cap in zip(cycle_counts, capacities):
        e = (n - 1) * cap * scale  # extra-cycle area for this candidate
        allocated_extra += e
        areas.append(e / (n - 1))  # physical link area
    residual = np.clip(m_total - allocated_extra, 0.0, None)
    return areas, residual


def run_derivation(
    annual_harvested: dict[tuple[str, str], np.ndarray],
    footprint: dict[str, np.ndarray],
    crop_area: dict[tuple[str, str], np.ndarray],
    zone: dict[str, np.ndarray],
    rice_support: dict[str, np.ndarray],
    combos: list[dict],
) -> tuple[dict[tuple[str, str], np.ndarray], np.ndarray, pd.DataFrame]:
    """Run the full per-cell attribution over the global grid.

    Parameters mirror the loaded rasters (all same shape). ``combos`` is a list of
    dicts ``{"name", "crops": [...], "water_supply": "i"|"r"}``. Returns:
      * ``area_rasters`` -- physical link area ``A`` (ha) per (combo name, ws).
      * ``residual`` -- unattributed extra-cycle area (ha).
      * ``stats`` -- per-(combo, ws) global attributed physical and extra-cycle area.
    """
    # Magnitude: total harvested (all crops, both systems) minus combined footprint.
    h_total = np.sum(list(annual_harvested.values()), axis=0)
    foot = np.clip(footprint["ir"], 0.0, None) + np.clip(footprint["rf"], 0.0, None)
    m_total = np.clip(h_total - foot, 0.0, None)

    capacities = [
        candidate_capacity(
            c["crops"],
            c["water_supply"],
            crop_area,
            zone[c["water_supply"]],
            rice_support,
        )
        for c in combos
    ]
    cycle_counts = [len(c["crops"]) for c in combos]

    areas, residual = allocate(m_total, capacities, cycle_counts)

    area_rasters: dict[tuple[str, str], np.ndarray] = {}
    records: list[dict] = []
    for c, area in zip(combos, areas):
        key = (c["name"], c["water_supply"])
        area_rasters[key] = area
        n = len(c["crops"])
        records.append(
            {
                "combination": c["name"],
                "water_supply": c["water_supply"],
                "cycles": n,
                "physical_area_mha": area.sum() / 1e6,
                "extra_cycle_area_mha": (n - 1) * area.sum() / 1e6,
            }
        )
    stats = pd.DataFrame.from_records(records)
    return area_rasters, residual, stats


def build_crop_area(
    annual_harvested: dict[tuple[str, str], np.ndarray],
    glade_to_mirca: dict[str, str],
) -> dict[tuple[str, str], np.ndarray]:
    """Map MIRCA (crop, ir/rf) harvested arrays to GLADE (crop, i/r) supports."""
    ws_map = {"i": "ir", "r": "rf"}
    return {
        (glade_crop, gws): annual_harvested[(mirca_crop, mws)]
        for glade_crop, mirca_crop in glade_to_mirca.items()
        for gws, mws in ws_map.items()
    }


def discover_combinations(
    stats: pd.DataFrame,
    seed_names: set[str],
    floor_mha: float,
    max_combos: int,
) -> dict[str, dict]:
    """Select the combination set and emit a ``config["multiple_cropping"]`` block.

    A combination is kept if it is in the agronomic seed set or its global
    attributed extra-cycle area clears ``floor_mha``; the kept set is capped at
    ``max_combos`` (largest first). Within a kept combination, only water supplies
    with positive attributed area are listed.
    """
    per_combo = stats.groupby("combination")["extra_cycle_area_mha"].sum()
    kept = [
        name
        for name in per_combo.index
        if name in seed_names or per_combo[name] >= floor_mha
    ]
    kept = sorted(kept, key=lambda n: -per_combo[n])[:max_combos]

    combos_cfg: dict[str, dict] = {}
    for name in kept:
        rows = stats[stats["combination"] == name]
        supplies = sorted(
            rows.loc[rows["physical_area_mha"] > 0, "water_supply"].unique()
        )
        if not supplies:
            continue
        crops = COMBO_CROPS[name]
        combos_cfg[name] = {"crops": list(crops), "water_supplies": supplies}
    return combos_cfg


# Agronomic seed set, in GLADE crop names.
COMBO_CROPS: dict[str, list[str]] = {
    "rice_wheat": ["wetland-rice", "wheat"],
    "double_rice": ["wetland-rice", "wetland-rice"],
    "triple_rice": ["wetland-rice", "wetland-rice", "wetland-rice"],
    "rice_maize": ["wetland-rice", "maize"],
    "wheat_maize": ["wheat", "maize"],
    "wheat_soybean": ["wheat", "soybean"],
    "maize_soybean": ["maize", "soybean"],
    "cotton_wheat": ["cotton", "wheat"],
}


def write_outputs(
    area_rasters: dict[tuple[str, str], np.ndarray],
    residual: np.ndarray,
    combos_cfg: dict[str, dict],
    profile: dict,
    out_dir: Path,
) -> None:
    """Write combinations.yaml, per-(combo, ws) baseline rasters, and the residual."""
    baseline_dir = out_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    raster_profile = {
        **profile,
        "count": 1,
        "dtype": "float32",
        "nodata": 0.0,
        "compress": "deflate",
    }

    for name, entry in combos_cfg.items():
        for ws in entry["water_supplies"]:
            arr = area_rasters[(name, ws)].astype(np.float32)
            with rasterio.open(
                baseline_dir / f"{name}_{ws}.tif", "w", **raster_profile
            ) as dst:
                dst.write(arr, 1)

    with rasterio.open(
        out_dir / "residual_multicrop.tif", "w", **raster_profile
    ) as dst:
        dst.write(residual.astype(np.float32), 1)

    header = (
        "# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek\n"
        "#\n"
        "# SPDX-License-Identifier: CC-BY-4.0\n"
        "#\n"
        "# Multi-cropping combination set discovered from MIRCA-OS 2020 by\n"
        "# workflow/scripts/derive_mirca_multicropping.py (a Snakemake\n"
        "# checkpoint). Merged over config['multiple_cropping'] to form the\n"
        "# effective combination set. Do not hand-edit.\n"
    )
    with open(out_dir / "combinations.yaml", "w") as fh:
        fh.write(header)
        yaml.safe_dump(combos_cfg, fh, sort_keys=True, default_flow_style=False)


def _annual_key(mirca_crop: str, mws: str) -> str:
    """Snakemake input key for an annual raster (spaces are not valid in keys)."""
    return f"annual_{mirca_crop.replace(' ', '_')}_{mws}"


def _load_inputs(inp, mirca_crops, glade_to_mirca):
    """Load all rasters from a snakemake-style input mapping."""
    annual = {
        (mc, mws): load_tif(inp[_annual_key(mc, mws)])
        for mc in mirca_crops
        for mws in ("ir", "rf")
    }
    footprint = {ws: load_tif(inp[f"footprint_{ws}"]) for ws in ("ir", "rf")}
    zone = {ws: load_tif(inp[f"zone_{ws}"]) for ws in ("i", "r")}
    rice_support: dict[str, np.ndarray] = {}
    for gws, mws in {"i": "ir", "r": "rf"}.items():
        rice_support[gws] = load_subcrop_maxmonth(inp[f"rice2_{mws}"])
        rice_support[gws + "3"] = load_subcrop_maxmonth(inp[f"rice3_{mws}"])
    return annual, footprint, zone, rice_support


def main() -> None:
    inp = dict(snakemake.input.items())  # type: ignore[name-defined]
    params = snakemake.params  # type: ignore[name-defined]
    out_dir = Path(snakemake.output.out_dir)  # type: ignore[name-defined]

    mapping = pd.read_csv(inp["concordance"], comment="#")
    mapping["glade_crop"] = mapping["glade_crop"].fillna("").astype(str).str.strip()
    mapping["mirca_crop"] = mapping["mirca_crop"].astype(str).str.strip()
    mirca_crops = mapping["mirca_crop"].tolist()
    glade_to_mirca = {
        row.glade_crop: row.mirca_crop for row in mapping.itertuples() if row.glade_crop
    }

    annual, footprint, zone, rice_support = _load_inputs(
        inp, mirca_crops, glade_to_mirca
    )
    crop_area = build_crop_area(annual, glade_to_mirca)

    seed_names = set(params.seed_combinations)
    combos = [
        {"name": name, "crops": COMBO_CROPS[name], "water_supply": ws}
        for name in seed_names
        for ws in ("i", "r")
    ]

    area_rasters, residual, stats = run_derivation(
        annual, footprint, crop_area, zone, rice_support, combos
    )

    combos_cfg = discover_combinations(
        stats,
        seed_names,
        float(params.coverage_floor_mha),
        int(params.max_combinations),
    )

    with rasterio.open(inp["footprint_ir"]) as src:
        profile = src.profile

    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(area_rasters, residual, combos_cfg, profile, out_dir)

    h_total = np.sum(list(annual.values()), axis=0)
    foot = footprint["ir"] + footprint["rf"]
    m_total = float(np.clip(h_total - foot, 0.0, None).sum()) / 1e6
    resid = float(residual.sum()) / 1e6
    print(
        f"Multi-cropping derivation: M_total={m_total:.1f} Mha, "
        f"attributed={m_total - resid:.1f} Mha, residual={resid:.1f} Mha "
        f"({resid / m_total * 100:.0f}% of M); {len(combos_cfg)} combinations kept"
    )


if __name__ == "__main__":
    main()
