# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Download land cover data using the ECMWF datastores client.

This is a library module used by the maintainer tool
``tools/mirror_land_cover.py`` (which imports :func:`main`). It is no longer
wired into a Snakemake rule: ordinary builds fetch the mirrored data from
Zenodo. Credentials are passed in by the caller, sourced from environment
variables (ECMWF_DATASTORES_URL / ECMWF_DATASTORES_KEY) or config/secrets.yaml.
"""

from pathlib import Path

from ecmwf.datastores import Client


def main(dataset: str, request: dict, output: Path, url: str, key: str) -> None:
    """Download land cover dataset.

    Parameters
    ----------
    dataset : str
        The dataset identifier (e.g., "satellite-land-cover").
    request : dict
        The request parameters including variable, year, and version.
    output : Path
        The output archive path (ZIP containing the NetCDF payload).
    url : str
        ECMWF datastores API URL.
    key : str
        ECMWF datastores API key.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    client = Client(url=url, key=key)
    client.retrieve(dataset, request, target=str(output))
