# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from pathlib import Path

import geopandas as gpd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def _remove_small_islands(
    gdf: gpd.GeoDataFrame, min_area_m2: float
) -> gpd.GeoDataFrame:
    """Remove small polygons from geometries by area threshold in projected CRS.

    Expects geometries in projected meters CRS (e.g., EPSG:6933).
    """
    from shapely.geometry import MultiPolygon, Polygon

    def _filter_geom(geom):
        if geom is None or geom.is_empty:
            return geom
        if isinstance(geom, MultiPolygon):
            kept = [p for p in geom.geoms if p.area >= min_area_m2]
            return MultiPolygon(kept) if kept else Polygon()
        if isinstance(geom, Polygon):
            return geom if geom.area >= min_area_m2 else Polygon()
        return geom

    gdf = gdf.copy()
    gdf.geometry = gdf.geometry.apply(_filter_geom)
    gdf = gdf[~gdf.geometry.is_empty]
    return gdf


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    gdf = gpd.read_file(snakemake.input[0])

    # Simplify geometries
    gdf = gdf.to_crs("EPSG:6933")
    min_area = snakemake.params.simplify_min_area_km * 1e6
    gdf = _remove_small_islands(gdf, min_area)
    gdf.geometry = gdf.geometry.simplify_coverage(
        tolerance=snakemake.params.simplify_tolerance_km * 1e3
    )
    gdf = gdf.to_crs("EPSG:4326")

    # Write a compact GPKG with only the simplified ADM_1 layer
    out_path = Path(snakemake.output[0])  # type: ignore[name-defined]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, layer="ADM_1", driver="GPKG")
    logger.info("Wrote simplified ADM_1 to %s", out_path)
