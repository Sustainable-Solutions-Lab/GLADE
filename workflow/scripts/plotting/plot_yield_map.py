# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot gridded crop or pasture yields on an Equal Earth map."""

import logging
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib

matplotlib.use("pdf")
from matplotlib import colormaps
from matplotlib.colors import Normalize
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator
import numpy as np
import pandas as pd
import rasterio
import xarray as xr

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _load_geotiff(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load GeoTIFF and convert to coordinate arrays for consistent plotting."""
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        nodata = src.nodata
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)
        transform = src.transform

    height, width = data.shape

    # Create coordinate arrays from the transform
    xmin = transform.c
    ymax = transform.f
    x_coords = xmin + np.arange(width) * transform.a + transform.a / 2
    y_coords = ymax + np.arange(height) * transform.e + transform.e / 2

    return data, x_coords, y_coords


def _load_netcdf(
    path: str, variable: str | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ds = xr.open_dataset(path, mode="r", decode_times=False)
    try:
        if variable is None:
            variables = list(ds.data_vars)
            if len(variables) != 1:
                raise ValueError(
                    f"Dataset {path} has multiple variables; specify params['variable']"
                )
            variable = variables[0]
        data_array = ds[variable].astype(float)
        if "time" in data_array.dims:
            data_array = data_array.mean(dim="time", skipna=True)
        lat_name = next(
            (d for d in data_array.dims if d.lower().startswith("lat")), None
        )
        lon_name = next(
            (d for d in data_array.dims if d.lower().startswith("lon")), None
        )
        if lat_name is None or lon_name is None:
            raise ValueError("Dataset must contain latitude/longitude dimensions")
        lat = np.asarray(data_array[lat_name])
        lon = np.asarray(data_array[lon_name])
        data = np.asarray(data_array)
    finally:
        ds.close()
    if lat.ndim != 1 or lon.ndim != 1 or data.ndim != 2:
        raise ValueError("Expected 2D data with 1D lat/lon coordinates")
    return data, lon, lat


def _compute_limits(
    arr: np.ndarray, vmin: float | None, vmax: float | None
) -> tuple[float, float]:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0, 1.0
    min_val = float(np.min(finite)) if vmin is None else float(vmin)
    max_val = float(np.max(finite)) if vmax is None else float(vmax)
    if np.isclose(max_val, min_val):
        max_val = min_val + 1e-6
    return min_val, max_val


def _plot_yield_data(
    data: np.ndarray,
    lon: np.ndarray,
    lat: np.ndarray,
    *,
    title: str,
    unit: str,
    cmap_name: str,
    factor: float,
    output_path: Path,
    vmin: float | None,
    vmax: float | None,
) -> None:
    arr = data * factor
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        arr = arr[::-1, :]

    min_val, max_val = _compute_limits(arr, vmin, vmax)
    cmap = colormaps.get_cmap(cmap_name)
    norm = Normalize(vmin=min_val, vmax=max_val)

    fig, ax = plt.subplots(
        figsize=(12, 6.5), dpi=150, subplot_kw={"projection": ccrs.EqualEarth()}
    )
    ax.set_facecolor("#f7f9fb")
    ax.set_global()

    # Choose plotting method based on data resolution
    # High-resolution data (>1000 pixels in either dimension) uses imshow for speed
    # Lower-resolution data uses pcolormesh for better visual quality
    if data.shape[0] > 1000 or data.shape[1] > 1000:
        # High-resolution: use imshow (faster)
        extent = [lon[0], lon[-1], lat[-1], lat[0]]
        img = ax.imshow(
            arr,
            origin="upper",
            extent=extent,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
        )
        logger.info("Using imshow for high-resolution data (%dx%d)", *data.shape)
    else:
        # Lower-resolution: use pcolormesh (better quality)
        lon2d, lat2d = np.meshgrid(lon, lat)
        img = ax.pcolormesh(
            lon2d,
            lat2d,
            arr,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            shading="auto",
        )
        logger.info("Using pcolormesh for lower-resolution data (%dx%d)", *data.shape)

    ax.set_title(title)
    ax.gridlines(
        draw_labels=False,
        linewidth=0.2,
        color="#888888",
        alpha=0.4,
        linestyle="--",
        xlocs=FixedLocator(range(-180, 181, 60)),
        ylocs=FixedLocator(range(-60, 61, 30)),
    )
    cb = fig.colorbar(img, ax=ax, orientation="horizontal", fraction=0.045, pad=0.08)
    cb.set_label(f"Yield ({unit})")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved yield map to %s", output_path)


def _load_conversion_table(path: str) -> dict[str, float]:
    try:
        df = pd.read_csv(path, comment="#")
    except FileNotFoundError:
        return {}
    df = df.dropna(subset=["code"])
    df = df.dropna(subset=["factor_to_t_per_ha"])
    return {
        str(row["code"]): float(row["factor_to_t_per_ha"]) for _, row in df.iterrows()
    }


def _load_irrigated_map(path: str) -> dict[str, str]:
    if not path:
        return {}
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        return {}
    df = df.dropna(subset=["code", "first_available"])
    df = df[df["first_available"].str.lower() != "none"]
    return {str(row["code"]): str(row["first_available"]) for _, row in df.iterrows()}


def _format_crop_label(item: str) -> str:
    return str(item).replace("-", " ").replace("_", " ").title()


def _determine_water_supply_for_title(
    code: str | None, supply: str, irrigated_map: dict[str, str]
) -> str:
    """Determine the actual water supply used for the plot title."""
    if supply != "i":
        return supply

    # For "i" request, determine what was actually used
    if code is None:
        return "i"
    return irrigated_map.get(code, "i")


def main() -> None:
    setup_script_logging(snakemake.log[0])

    params = snakemake.params  # type: ignore[name-defined]
    inputs = snakemake.input  # type: ignore[name-defined]
    item: str = params["item"]
    supply: str = params["supply"]
    unit: str = params.get("unit", "t/ha")
    cmap: str = params.get("cmap", "YlGn")
    gaez_code: str | None = params.get("gaez_code")

    conversions = _load_conversion_table(inputs.get("conversions"))
    irrigated_map = _load_irrigated_map(inputs.get("irrigated"))

    vmin = params.get("vmin")
    vmax = params.get("vmax")

    # The raster input is now correctly determined by the rule
    raster_path = inputs["raster"]

    if item == "pasture":
        factor = 1.0
        title = "Pasture Yield"
        variable = "yield-mgr-noirr"
    else:
        code = gaez_code
        # Determine actual water supply used (for title)
        water_supply = _determine_water_supply_for_title(code, supply, irrigated_map)

        factor = conversions.get(code, 0.001) if code is not None else 0.001
        suffix_map = {
            "i": "Irrigated",
            "r": "Rainfed",
            "g": "Groundwater irrigated",
            "s": "Surface irrigated",
            "d": "Drip irrigated",
        }
        suffix = suffix_map.get(water_supply, water_supply.upper())
        title = f"{_format_crop_label(item)} Yield ({suffix})"
        variable = None

    output_path = Path(snakemake.output.pdf)  # type: ignore[name-defined]

    # Load data - both formats now return the same structure
    raster_suffix = Path(raster_path).suffix.lower()
    if raster_suffix in {".tif", ".tiff"}:
        data, lon, lat = _load_geotiff(raster_path)
    else:
        data, lon, lat = _load_netcdf(raster_path, variable)

    # Use unified plotting approach for both data types
    _plot_yield_data(
        data,
        lon,
        lat,
        title=title,
        unit=unit,
        cmap_name=cmap,
        factor=factor,
        output_path=output_path,
        vmin=vmin,
        vmax=vmax,
    )


if __name__ == "__main__":
    main()
