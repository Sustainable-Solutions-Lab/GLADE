# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extract the lccs_class variable from the land cover dataset ZIP archive.

The downloaded land cover dataset contains multiple variables (lccs_class,
processed_flag, current_pixel_state, observation_count, change_count), but only
the lccs_class variable (land cover classification) is needed for the model.
This script extracts just that variable to reduce file size from ~2.2GB to ~440MB.
It operates on the ZIP archive distributed by CDS, which bundles the NetCDF file.

Snakemake passes the ``snakemake`` object into this module; no standalone CLI
usage is supported.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

import xarray as xr


def _extract_netcdf_to_temp(zip_path: Path) -> tuple[Path, TemporaryDirectory]:
    """Extract the single NetCDF member from ``zip_path`` into a temp dir."""
    with zipfile.ZipFile(zip_path) as zf:
        nc_members = [
            member for member in zf.namelist() if member.lower().endswith(".nc")
        ]
        if not nc_members:
            raise ValueError(f"No NetCDF files found inside {zip_path}")
        if len(nc_members) > 1:
            raise ValueError(
                f"Expected a single NetCDF file inside {zip_path}, found {len(nc_members)}: {nc_members}"
            )
        member = nc_members[0]

        tmp_dir = TemporaryDirectory()
        extracted_path = Path(zf.extract(member, path=tmp_dir.name))
        return extracted_path, tmp_dir


def main(input_path: Path, output_path: Path) -> None:
    """Extract lccs_class variable from land cover archive.

    Parameters
    ----------
    input_path : Path
        Path to the land cover ZIP archive containing the full NetCDF file.
    output_path : Path
        Path for the output NetCDF file containing only lccs_class.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cleanup_dir: TemporaryDirectory | None = None
    try:
        if input_path.suffix.lower() != ".zip":
            raise ValueError(f"Expected ZIP input, got {input_path}")

        extracted_nc, cleanup_dir = _extract_netcdf_to_temp(input_path)

        with xr.open_dataset(extracted_nc) as ds:
            lccs_class = ds["lccs_class"]

            encoding = {
                "lccs_class": {
                    "zlib": True,
                    "complevel": 5,
                    "dtype": "uint8",  # Land cover classes are small integers
                }
            }

            lccs_class.to_netcdf(output_path, encoding=encoding)
    finally:
        if cleanup_dir is not None:
            cleanup_dir.cleanup()


if __name__ == "__main__":
    main(
        input_path=Path(snakemake.input[0]),
        output_path=Path(snakemake.output[0]),
    )
