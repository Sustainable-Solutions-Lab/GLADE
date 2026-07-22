# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Water and fertilizer-related data preparation rules.

Water availability source (``water.data.availability``):
- "aware": Uses AWARE2.0 (Seitfudem et al. 2025, WaterGAP2.2e) naturalised
  availability and a convex water-scarcity supply curve.
- "current_use": Uses Huang et al. (2018) gridded irrigation water withdrawals
  (validation/benchmarking; a single zero-scarcity supply tier).

Both sources emit a ``region_water_tiers.csv`` describing the regional water
supply as one or more tiers (capacity in Mm3, marginal scarcity characterisation
factor); ``aware`` resolves it to a convex merit-order curve while ``current_use``
emits a single zero-CF tier reproducing a hard availability cap.
"""


_WATERGAP_ISIMIP = (
    "data/downloads/watergap/"
    "watergap2-2e_gswp3-w5e5_obsclim_histsoc_default_{var}_global_monthly_1901_2019.nc"
)
_WATERGAP_CONTINENTALAREA = (
    "data/downloads/watergap/"
    "watergap22e_gswp3-w5e5_continentalarea_histsoc_static.nc"
)


rule prepare_fertilizer_application_rates:
    input:
        fubc_data="data/bundled/doi_10_5061_dryad_2rbnzs7qh__v20250311/FUBC_1_to_9_data.csv",
        mapping="data/curated/ifa_fubc_crop_mapping.csv",
    output:
        "<processing>/{name}/fertilizer_application_rates.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/prepare_fertilizer_application_rates.log",
    benchmark:
        "<benchmarks>/{name}/prepare_fertilizer_application_rates.tsv"
    script:
        "../scripts/prepare_fertilizer_application_rates.py"


rule derive_global_fertilizer_rates:
    input:
        fertilizer_rates="<processing>/{name}/fertilizer_application_rates.csv",
    params:
        n_percentile=config["fertilizer"]["n_percentile"],
        crops=config["crops"],
        proxy_rates=config["fertilizer"]["proxy_rates"],
    output:
        "<processing>/{name}/global_fertilizer_n_rates.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/derive_global_fertilizer_rates.log",
    benchmark:
        "<benchmarks>/{name}/derive_global_fertilizer_rates.tsv"
    script:
        "../scripts/derive_global_fertilizer_rates.py"


def crop_yield_file_list(w):
    return list(yield_inputs(w).values())


# Rule for the convex water-scarcity supply curve. The volumes (per
# region-month) are WaterGAP's irrigation surface consumption; its grid cells
# set the within-region AWARE-basin allocation, while AWARE contributes the CF
# curve.
rule build_region_water_aware:
    input:
        intermediate="data/downloads/aware2/AWARE20_Intermediate_Variables.xlsx",
        native_cfs="data/downloads/aware2/AWARE20_Native_CFs.xlsx",
        basins="data/downloads/aware2/AWARE20_Native_CFs_geospatial.gpkg",
        regions="<processing>/{name}/regions.geojson",
        watergap_surface="<processing>/{name}/water/watergap/region_watergap_surface.csv",
        watergap_pirruse=_WATERGAP_ISIMIP.format(var="pirruse"),
        watergap_pirrusegw=_WATERGAP_ISIMIP.format(var="pirrusegw"),
        watergap_continentalarea=_WATERGAP_CONTINENTALAREA,
    params:
        surface_start=config["water"]["data"]["surface_reference_start"],
        surface_end=config["water"]["data"]["surface_reference_end"],
    output:
        monthly_region="<processing>/{name}/water/aware/monthly_region_water.csv",
        region_growing="<processing>/{name}/water/aware/region_growing_season_water.csv",
        tiers="<processing>/{name}/water/aware/region_water_tiers.csv",
    group:
        "prep"
    resources:
        runtime="15m",
        mem_mb=2000,
    log:
        "<logs>/{name}/build_region_water_aware.log",
    benchmark:
        "<benchmarks>/{name}/build_region_water_aware.tsv"
    script:
        "../scripts/build_region_water_aware.py"


# Rule for current water use (Huang et al. 2018 gridded irrigation data)
rule build_region_water_current_use:
    input:
        nc="data/downloads/huang_irrigation_water.nc",
        regions="<processing>/{name}/regions.geojson",
        crop_yields=crop_yield_file_list,
    params:
        reference_year=config["water"]["data"]["huang_reference_year"],
    output:
        monthly_region="<processing>/{name}/water/current_use/monthly_region_water.csv",
        region_growing="<processing>/{name}/water/current_use/region_growing_season_water.csv",
        tiers="<processing>/{name}/water/current_use/region_water_tiers.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=400,
    log:
        "<logs>/{name}/build_region_water_current_use.log",
    benchmark:
        "<benchmarks>/{name}/build_region_water_current_use.tsv"
    script:
        "../scripts/process_huang_irrigation_water.py"


# Rule for regional WaterGAP 2.2e (ISIMIP3a) water fields: irrigation surface
# availability (pirruse - pirrusegw, the cap for the AWARE curve) and the
# groundwater bands (mined = storage trend; renewable = pirrusegw - mined).
rule build_region_watergap:
    input:
        groundwstor=_WATERGAP_ISIMIP.format(var="groundwstor"),
        pirruse=_WATERGAP_ISIMIP.format(var="pirruse"),
        pirrusegw=_WATERGAP_ISIMIP.format(var="pirrusegw"),
        continentalarea=_WATERGAP_CONTINENTALAREA,
        regions="<processing>/{name}/regions.geojson",
    params:
        trend_start=config["water"]["data"]["groundwater_trend_start"],
        trend_end=config["water"]["data"]["groundwater_trend_end"],
        surface_start=config["water"]["data"]["surface_reference_start"],
        surface_end=config["water"]["data"]["surface_reference_end"],
    output:
        surface="<processing>/{name}/water/watergap/region_watergap_surface.csv",
        depletion="<processing>/{name}/water/watergap/region_groundwater_depletion.csv",
        region_agri="<processing>/{name}/water/watergap/region_agri_consumption.csv",
        demand="<processing>/{name}/water/watergap/region_watergap_demand.csv",
    group:
        "prep"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/build_region_watergap.log",
    benchmark:
        "<benchmarks>/{name}/build_region_watergap.tsv"
    script:
        "../scripts/build_region_watergap.py"


# MIRCA-OS v2 crops with more than one irrigated sub-crop cycle in the monthly
# grids; every other base crop ships a single grid without a cycle digit. Used
# to expand each mapped base crop into its sub-crop grid filenames.
_MIRCA_SUBCROP_CYCLES = {"Rice": 3, "Wheat": 2}


def mirca_calendar_grids(w):
    """2015 irrigated monthly growing-area grids for every mapped MIRCA crop.

    Reads the crop concordance plus the calendar-only supplement and expands
    each non-dropped MIRCA base crop into its sub-crop grid(s) (``Rice1/2/3``,
    ``Wheat1/2``, else the base label), keyed ``nc_{subcrop}`` for
    ``build_mirca_crop_calendar``.
    """
    import pandas as pd

    mapping = pd.concat(
        [
            pd.read_csv("data/curated/mirca_os_crop_mapping.csv", comment="#"),
            pd.read_csv("data/curated/mirca_os_calendar_supplement.csv", comment="#"),
        ],
        ignore_index=True,
    )
    mapping = mapping[mapping["glade_crop"].notna() & (mapping["glade_crop"] != "")]
    grids = {}
    for base in mapping["mirca_crop"].unique():
        n = _MIRCA_SUBCROP_CYCLES.get(base, 1)
        labels = [f"{base}{i}" for i in range(1, n + 1)] if n > 1 else [base]
        for label in labels:
            grids[f"nc_{label}"] = (
                f"data/downloads/mirca_os/grids/monthly/MIRCA-OS_{label}_2015_ir.nc"
            )
    return grids


# Observed irrigated crop calendar: per (region, crop) monthly water-demand
# shares from MIRCA-OS 2015 monthly growing-area grids, retimed by iterative
# proportional fitting so that region-month demand totals follow WaterGAP's
# monthly irrigation requirement (pirruse) while each crop's annual total and
# observed season (structural zeros) are preserved. Places irrigation demand
# in the observed months at requirement weighting (single-crop links and
# multi-cropping cycles), consistent with WaterGAP's reservoir-regulated
# monthly surface delivery, instead of GAEZ's yield-maximising potential
# calendar with uniform within-season weighting.
rule build_mirca_crop_calendar:
    input:
        unpack(mirca_calendar_grids),
        mapping="data/curated/mirca_os_crop_mapping.csv",
        supplement="data/curated/mirca_os_calendar_supplement.csv",
        demand="<processing>/{name}/water/watergap/region_watergap_demand.csv",
        crop_yields=crop_yield_file_list,
        regions="<processing>/{name}/regions.geojson",
    output:
        "<processing>/{name}/water/mirca_crop_calendar.csv",
    group:
        "prep"
    resources:
        runtime="10m",
        mem_mb=3000,
    log:
        "<logs>/{name}/build_mirca_crop_calendar.log",
    benchmark:
        "<benchmarks>/{name}/build_mirca_crop_calendar.tsv"
    script:
        "../scripts/build_mirca_crop_calendar.py"


def water_availability_inputs(w):
    """The availability source's monthly/growing/tier files (aware or current_use)."""
    availability = config["water"]["data"]["availability"]
    base = f"<processing>/{w.name}/water/{availability}"
    return {
        "monthly": f"{base}/monthly_region_water.csv",
        "growing": f"{base}/region_growing_season_water.csv",
        "tiers": f"{base}/region_water_tiers.csv",
    }


def water_groundwater_input(w):
    """Groundwater depletion table and consumption anchor (groundwater only)."""
    if config["water"]["supply"]["groundwater"]:
        return {
            "groundwater": f"<processing>/{w.name}/water/watergap/region_groundwater_depletion.csv",
            "region_agri": f"<processing>/{w.name}/water/watergap/region_agri_consumption.csv",
        }
    return {}


# Compose the scenario-agnostic water-supply tables from the selected
# availability source: per-period surface tiers (source "renewable") and,
# with supply.groundwater, the annual per-region groundwater bands.
rule compose_water_supply:
    input:
        unpack(water_availability_inputs),
        unpack(water_groundwater_input),
    params:
        scarcity_tiers=config["water"]["supply"]["scarcity_tiers"],
        groundwater=config["water"]["supply"]["groundwater"],
        availability=config["water"]["data"]["availability"],
        temporal_resolution=config["water"]["temporal_resolution"],
        consumed_fraction=config["water"]["irrigation"]["consumed_fraction"],
        groundwater_ceiling_factor=config["water"]["supply"][
            "groundwater_ceiling_factor"
        ],
    output:
        monthly_region="<processing>/{name}/water/monthly_region_water.csv",
        region_growing="<processing>/{name}/water/region_growing_season_water.csv",
        tiers="<processing>/{name}/water/region_water_tiers.csv",
        groundwater_bands="<processing>/{name}/water/region_groundwater_bands.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=400,
    log:
        "<logs>/{name}/compose_water_supply.log",
    benchmark:
        "<benchmarks>/{name}/compose_water_supply.tsv"
    script:
        "../scripts/compose_water_supply.py"
