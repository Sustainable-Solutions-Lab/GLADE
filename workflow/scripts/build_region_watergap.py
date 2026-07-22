"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Aggregate WaterGAP 2.2e (ISIMIP3a) fields to model regions: the irrigation
surface-water availability that caps the AWARE scarcity curve, and the renewable
and non-renewable (mined) groundwater bands.

All fields are the standard ISIMIP3a WaterGAP2.2e output, ``obsclim`` climate /
``histsoc`` (with human water use) setup, ``gswp3-w5e5`` forcing, monthly
1901-2019, 0.5 degree. Irrigation-sector, source-split water use is published
directly, on both a withdrawal and a consumption basis:

- ``continentalarea`` (km2): WaterGAP's static continental area, including
  land and surface-water bodies but excluding ocean. It is the required volume
  conversion area for WaterGAP's flux and storage fields.
- ``groundwstor`` (mm): groundwater storage compartment. Its negative long-term
  trend is groundwater depletion / mining (Doll et al. 2014).
- ``pirruse`` (kg m-2 s-1 = mm/s): potential irrigation water consumption (the
  evapotranspired portion), all sources.
- ``pirrusegw``: the part of ``pirruse`` supplied from groundwater.
- ``ptotusegw``: potential groundwater consumption of *all* sectors; the
  denominator of irrigation's share of groundwater abstraction.

The model works on a consumption basis (crops draw beneficial ET, delivered from
the consumption-basis pool), so the ``use`` (consumption) variables are the right
ones. From them:

- **irrigation surface availability** = ``pirruse - pirrusegw`` (per region and
  month). This is WaterGAP's assessment of how much of irrigation's consumptive
  demand its detailed water allocation supplies from surface water. It replaces
  AWARE's basin-discharge availability (which counts through-flow river discharge
  as divertible and so hugely overstates the accessible surface in
  groundwater-dependent basins such as the Ogallala). Crucially it is kept
  *monthly*: WaterGAP's ``histsoc`` runs operate every GRanD reservoir >= 0.5 km3
  (Hanasaki scheme), so the monthly timing of ``pirruse - pirrusegw`` is
  regulated, demand-timed delivery -- reservoirs carry wet-season discharge into
  the irrigation season inside WaterGAP. AWARE's monthly shape is unregulated
  discharge timing and strands that delivery in the wet months. The AWARE
  scarcity (CF) curve is kept; the per region-month volumes are rescaled to this
  envelope in ``build_region_water_aware.py``.
- **mined groundwater** = the groundwater-storage decline. The trend reflects
  all users, so irrigation's part is attributed by its share of potential
  groundwater consumption (``pirrusegw / ptotusegw``, same basis and window);
  a basin mined by municipal or industrial pumping then no longer zeroes
  irrigation's renewable band.
- **renewable groundwater** = ``max(pirrusegw - mined_irrigation, 0)``: the
  recharged part of irrigation groundwater consumption.

Outputs (keyed by model ``region``):

- ``region_watergap_surface.csv``: ``region, month, surface_consumption_mm3`` --
  monthly climatological irrigation surface consumption, the availability
  envelope for the AWARE curve;
- ``region_groundwater_depletion.csv``: ``region, mined_mm3,
  irrigation_gw_share, mined_irrigation_mm3, renewable_gw_mm3`` -- the
  renewable-groundwater volume anchor (and mining diagnostics) consumed by
  ``build_region_water_aware.py`` and ``compose_water_supply.py``;
- ``region_agri_consumption.csv``: ``region, agri_consumption_m3`` -- annual
  total irrigation consumption (``pirruse``), the demand anchor for ``eta_c``
  and the mining ceiling. Replaces the AWARE 2019 ``agri_pHWC`` anchor so that
  every volume (supply envelope, groundwater bands, demand anchor) comes from
  one WaterGAP simulation and window; AWARE then contributes the scarcity (CF)
  valuation and its native basin geometry.
- ``region_watergap_demand.csv``: ``region, month, irrigation_consumption_mm3``
  -- the monthly resolution of the same ``pirruse`` climatology: WaterGAP's
  demand-timed irrigation requirement (net of effective precipitation). Used to
  retime crop-calendar demand shares so that region-month demand totals are
  consistent with the supply envelope above (``build_mirca_crop_calendar.py``).

Reference:
    Doll et al. (2014). Global-scale assessment of groundwater depletion and
    related groundwater abstractions. Water Resources Research, 50, 5698-5720.
    Muller Schmied et al. (2024). WaterGAP v2.2e. Geosci. Model Dev., 17, 8817.
"""

from pathlib import Path

from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

MM_TO_M = 1e-3
MM3_PER_M3 = 1e-6
M2_PER_KM2 = 1e6
WATERGAP_START_YEAR = 1901  # first year of the WaterGAP 2.2e monthly series
# Climatological month lengths (Feb averaged over leap years), summing to 365.25.
SECONDS_PER_MONTH = (
    np.array([31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]) * 24 * 3600.0
)

# WGS84 for the exactextract raster source.
_WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)


def load_continental_area(path: str, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Load WaterGAP's continental cell area as m2 on the requested grid."""
    ds = xr.open_dataset(path, decode_times=False)
    area = ds["continentalarea"].isel(time=0)
    area_lat = ds["lat"].values.astype(float)
    area_lon = ds["lon"].values.astype(float)
    values = area.values.astype(float)
    ds.close()

    if not np.array_equal(lat, area_lat) or not np.array_equal(lon, area_lon):
        raise ValueError("WaterGAP continental area does not share the data grid")
    return np.where(np.isfinite(values), values, 0.0) * M2_PER_KM2


def compute_depletion_raster(
    groundwstor_path: str,
    continental_area_path: str,
    trend_start: int,
    trend_end: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-cell groundwater depletion (m3/yr), latitudes and longitudes.

    Depletion is the negative linear trend of annual-mean groundwater storage
    over ``[trend_start, trend_end]`` (inclusive), converted from mm/yr to a
    volume via cell area. Cells with a non-negative trend (stable or recovering
    storage) contribute zero.
    """
    ds = xr.open_dataset(groundwstor_path, decode_times=False)
    storage = ds["groundwstor"]  # (time, lat, lon), kg m-2 = mm
    lat = ds["lat"].values.astype(float)
    lon = ds["lon"].values.astype(float)

    years = np.arange(trend_start, trend_end + 1)
    annual = np.empty((years.size, lat.size, lon.size), dtype=float)
    for i, year in enumerate(years):
        start = (year - WATERGAP_START_YEAR) * 12
        annual[i] = np.nanmean(storage.isel(time=slice(start, start + 12)).values, 0)
    ds.close()

    # Ordinary-least-squares slope per cell (mm/yr).
    x = years - years.mean()
    slope = (x[:, None, None] * (annual - annual.mean(0))).sum(0) / (x**2).sum()

    depletion_mm_yr = np.where(slope < 0, -slope, 0.0)
    continental_area_m2 = load_continental_area(continental_area_path, lat, lon)
    depletion_m3 = depletion_mm_yr * MM_TO_M * continental_area_m2
    return depletion_m3, lat, lon


def compute_monthly_flux_raster(
    path: str,
    variable: str,
    continental_area_path: str,
    reference_start: int,
    reference_end: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return a WaterGAP flux variable as per-cell monthly volumes (m3/month).

    ``variable`` is a monthly water-use flux (kg m-2 s-1 = mm/s), e.g. ``pirruse``
    or ``pirrusegw``. Averaged into a 12-month climatology over
    ``[reference_start, reference_end]`` (inclusive), clipped at zero per cell-month
    (negative cells are net returns / recharge) and converted to volumes via
    month length and cell area. Shape (12, nlat, nlon).
    """
    ds = xr.open_dataset(path, decode_times=False)
    flux = ds[variable]  # (time, lat, lon), kg m-2 s-1 = mm/s
    lat = ds["lat"].values.astype(float)
    lon = ds["lon"].values.astype(float)

    start = (reference_start - WATERGAP_START_YEAR) * 12
    end = (reference_end - WATERGAP_START_YEAR + 1) * 12
    window = flux.isel(time=slice(start, end)).values
    ds.close()
    clim_flux = np.nanmean(window.reshape(-1, 12, lat.size, lon.size), 0)

    volume_mm = np.clip(clim_flux, 0.0, None) * SECONDS_PER_MONTH[:, None, None]
    continental_area_m2 = load_continental_area(continental_area_path, lat, lon)
    volume_m3 = volume_mm * MM_TO_M * continental_area_m2[None, :, :]
    return volume_m3, lat, lon


def aggregate_to_regions(
    values_m3: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    regions_gdf: gpd.GeoDataFrame,
) -> pd.Series:
    """Coverage-weighted sum of a per-cell volume (m3/yr) into regions."""
    # Repair invalid geometries before the native exactextract call: it can
    # segfault on self-intersecting polygons (a handful can survive the region
    # GeoJSON round-trip at fine resolution). buffer(0) keeps clean polygonal
    # coverage; valid geometries are unchanged.
    invalid = ~regions_gdf.geometry.is_valid
    if invalid.any():
        regions_gdf = regions_gdf.copy()
        regions_gdf.loc[invalid, "geometry"] = regions_gdf.loc[
            invalid, "geometry"
        ].buffer(0)

    res = float(np.abs(np.diff(np.sort(np.unique(lon)))).min())
    arr = np.where(np.isfinite(values_m3), values_m3, 0.0)
    # Orient north-to-south for the raster source.
    if lat[0] < lat[-1]:
        arr = np.flipud(arr)
    ymin, ymax = float(lat.min()) - res / 2, float(lat.max()) + res / 2
    src = NumPyRasterSource(
        arr,
        xmin=float(lon.min()) - res / 2,
        xmax=float(lon.max()) + res / 2,
        ymin=ymin,
        ymax=ymax,
        srs_wkt=_WGS84_WKT,
    )
    result = exact_extract(
        src,
        regions_gdf.reset_index(),
        ["sum"],
        include_cols=["region"],
        output="pandas",
    )
    return result.set_index("region")["sum"].rename("value_m3")


if __name__ == "__main__":
    groundwstor_path: str = snakemake.input.groundwstor  # type: ignore[name-defined]
    continental_area_path: str = snakemake.input.continentalarea  # type: ignore[name-defined]
    pirruse_path: str = snakemake.input.pirruse  # type: ignore[name-defined]
    pirrusegw_path: str = snakemake.input.pirrusegw  # type: ignore[name-defined]
    ptotusegw_path: str = snakemake.input.ptotusegw  # type: ignore[name-defined]
    regions_path: str = snakemake.input.regions  # type: ignore[name-defined]
    trend_start: int = int(snakemake.params.trend_start)  # type: ignore[name-defined]
    trend_end: int = int(snakemake.params.trend_end)  # type: ignore[name-defined]
    surface_start: int = int(snakemake.params.surface_start)  # type: ignore[name-defined]
    surface_end: int = int(snakemake.params.surface_end)  # type: ignore[name-defined]
    surface_out: str = snakemake.output.surface  # type: ignore[name-defined]
    depletion_out: str = snakemake.output.depletion  # type: ignore[name-defined]
    agri_out: str = snakemake.output.region_agri  # type: ignore[name-defined]
    demand_out: str = snakemake.output.demand  # type: ignore[name-defined]

    regions_gdf = gpd.read_file(regions_path)[["region", "geometry"]]

    depletion_m3, lat, lon = compute_depletion_raster(
        groundwstor_path, continental_area_path, trend_start, trend_end
    )
    mined = aggregate_to_regions(depletion_m3, lat, lon, regions_gdf)

    def monthly_to_regions(path, variable):
        """(region x month) DataFrame of monthly volumes (m3)."""
        monthly_m3, lat, lon = compute_monthly_flux_raster(
            path, variable, continental_area_path, surface_start, surface_end
        )
        return pd.DataFrame(
            {
                m + 1: aggregate_to_regions(monthly_m3[m], lat, lon, regions_gdf)
                for m in range(12)
            }
        ).rename_axis(columns="month")

    irr_total = monthly_to_regions(pirruse_path, "pirruse")
    irr_gw = monthly_to_regions(pirrusegw_path, "pirrusegw")
    total_gw = monthly_to_regions(ptotusegw_path, "ptotusegw")

    region_index = pd.Index(sorted(regions_gdf["region"]), name="region")

    # Irrigation surface availability = total irrigation consumption minus the
    # groundwater-supplied part, per region-month. Clipped at zero (a region can
    # be fully groundwater-supplied, e.g. the Ogallala, where this is ~0). The
    # monthly timing carries WaterGAP's reservoir-regulated delivery.
    surface = (
        (irr_total.sub(irr_gw, fill_value=0.0) * MM3_PER_M3)
        .clip(lower=0.0)
        .reindex(region_index, fill_value=0.0)
        .stack()
        .rename("surface_consumption_mm3")
        .reset_index()
        .sort_values(["region", "month"])
    )
    Path(surface_out).parent.mkdir(parents=True, exist_ok=True)
    surface.to_csv(surface_out, index=False)

    # Groundwater (annual): mined from the storage trend, attributed to
    # irrigation by its share of all-sector groundwater consumption; renewable =
    # the recharged remainder of irrigation groundwater consumption
    # (pirrusegw - mined_irrigation). Regions with a storage decline but no
    # potential groundwater use (a climate-driven trend, not abstraction) get
    # share 0.
    irr_gw_annual = irr_gw.sum(axis=1)
    total_gw_annual = total_gw.sum(axis=1)
    share = (
        irr_gw_annual.div(total_gw_annual)
        .where(total_gw_annual > 0, 0.0)
        .clip(0.0, 1.0)
    )
    depletion = pd.DataFrame({"region": region_index}).assign(
        mined_mm3=lambda d: d["region"].map(mined * MM3_PER_M3).fillna(0.0),
        irrigation_gw_share=lambda d: d["region"].map(share).fillna(0.0),
        mined_irrigation_mm3=lambda d: d["mined_mm3"] * d["irrigation_gw_share"],
        renewable_gw_mm3=lambda d: (
            d["region"].map(irr_gw_annual * MM3_PER_M3).fillna(0.0)
            - d["mined_irrigation_mm3"]
        ).clip(lower=0.0),
    )
    Path(depletion_out).parent.mkdir(parents=True, exist_ok=True)
    depletion.to_csv(depletion_out, index=False)

    # Total irrigation consumption (pirruse, annual): the demand anchor for
    # eta_c and the groundwater mining ceiling, on the same basis, simulation
    # and reference window as the supply envelope above. Same schema as the
    # legacy AWARE anchor (region, agri_consumption_m3) so consumers are
    # source-agnostic.
    agri = pd.DataFrame({"region": region_index}).assign(
        agri_consumption_m3=lambda d: d["region"].map(irr_total.sum(axis=1)).fillna(0.0)
    )
    Path(agri_out).parent.mkdir(parents=True, exist_ok=True)
    agri.to_csv(agri_out, index=False)

    # Monthly resolution of the demand anchor: WaterGAP's requirement timing
    # (net of effective precipitation), consumed by the crop-calendar retiming.
    demand = (
        (irr_total * MM3_PER_M3)
        .reindex(region_index, fill_value=0.0)
        .stack()
        .rename("irrigation_consumption_mm3")
        .reset_index()
        .sort_values(["region", "month"])
    )
    Path(demand_out).parent.mkdir(parents=True, exist_ok=True)
    demand.to_csv(demand_out, index=False)
