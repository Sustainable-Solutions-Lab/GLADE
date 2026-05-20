# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Download SoilGrids organic carbon stock data via WCS at custom resolution.

This script uses the ISRIC WCS (Web Coverage Service) to download global soil
organic carbon stock data at a specified resolution. The native resolution is
250m, but this script resamples to a coarser resolution (e.g., 10km) to reduce
file size while maintaining global coverage.

Snakemake passes the ``snakemake`` object into this module; no standalone CLI
usage is supported.
"""

import logging
from pathlib import Path

from osgeo import gdal, osr
import requests

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)

# Enable GDAL exceptions for clearer failures
gdal.UseExceptions()
osr.UseExceptions()

# SoilGrids uses the Interrupted Goode Homolosine projection; EPSG:152160 is not
# available in our PROJ database, so we assign the projection via its proj4
# definition.
_SOILGRIDS_PROJ4 = "+proj=igh"


def _ensure_projection(path: Path) -> None:
    """Assign SoilGrids projection if the downloaded GeoTIFF lacks CRS.

    SoilGrids native data is always in Interrupted Goode Homolosine. If
    the downloaded file already carries a projection that differs from
    +proj=igh, the WCS server returned something unexpected; refuse to
    silently use it.
    """

    ds = gdal.Open(str(path), gdal.GA_Update)
    if ds is None:
        raise RuntimeError(f"Failed to open {path} for CRS assignment")
    try:
        existing = ds.GetProjectionRef()
        srs = osr.SpatialReference()
        srs.ImportFromProj4(_SOILGRIDS_PROJ4)
        if existing:
            existing_srs = osr.SpatialReference()
            existing_srs.ImportFromWkt(existing)
            if not existing_srs.IsSame(srs):
                raise RuntimeError(
                    f"SoilGrids download at {path} has unexpected projection "
                    f"(expected +proj=igh; got: {existing[:120]}...)"
                )
            return
        ds.SetProjection(srs.ExportToWkt())
    finally:
        ds = None


def download_via_wcs(
    coverage_id: str,
    target_resolution_m: float,
    output_path: Path,
) -> None:
    """Download SoilGrids coverage via WCS and resample to target resolution.

    Parameters
    ----------
    coverage_id : str
        WCS coverage ID (e.g., "ocs_0-30cm_mean").
    target_resolution_m : float
        Target resolution in meters (e.g., 10000 for 10km).
    output_path : Path
        Output GeoTIFF file path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # WCS endpoint
    # Coverage bounds from DescribeCoverage: x: -19949750 to 19861750, y: -6147500 to 8361000
    # Native grid: 159246 x 58034 pixels at 250m resolution
    # in EPSG:152160 (Interrupted Goode Homolosine)
    wcs_url = "https://maps.isric.org/mapserv"

    # Native resolution is 250m, calculate scaling factor for target resolution
    native_res_m = 250.0
    scale_factor = target_resolution_m / native_res_m

    # Calculate output dimensions
    native_width = 159246
    native_height = 58034
    output_width = int(native_width / scale_factor)
    output_height = int(native_height / scale_factor)

    logger.info("Downloading %s from ISRIC WCS...", coverage_id)
    logger.info(
        "Native resolution: %dm, target: %dm", native_res_m, target_resolution_m
    )
    logger.info("Scale factor: %.2fx", scale_factor)
    logger.info("Output dimensions: %d x %d pixels", output_width, output_height)

    # WCS 2.0 ScaleSize parameter to request at specific dimensions
    params = {
        "map": "/map/ocs.map",
        "service": "WCS",
        "version": "2.0.1",
        "request": "GetCoverage",
        "coverageid": coverage_id,
        "format": "image/tiff",
        # Request full global extent
        "subset": [
            "x(-19949750,19861750)",
            "y(-6147500,8361000)",
        ],
        # Scale to target resolution using ScaleSize extension
        "scalesize": f"x({output_width}),y({output_height})",
    }

    # Download the coverage
    logger.info("Requesting data from WCS...")
    response = requests.get(wcs_url, params=params, stream=True, timeout=1200)
    response.raise_for_status()

    # Save directly to output
    logger.info("Saving to %s...", output_path)
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Downloaded and saved %s", output_path)
    _ensure_projection(output_path)


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    download_via_wcs(
        coverage_id=snakemake.params.coverage_id,
        target_resolution_m=snakemake.params.target_resolution_m,
        output_path=Path(snakemake.output[0]),
    )
