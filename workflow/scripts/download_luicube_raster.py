# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Download a single LUIcube GeoTIFF from Zenodo via HTTP range requests.

The LUIcube dataset (Matej et al. 2025) provides global gridded land-use
intensity data at 30 arcsec resolution. Files are stored in ZIP archives on
Zenodo; ``remotezip`` extracts individual GeoTIFFs without downloading the
entire archive.

Snakemake passes the ``snakemake`` object into this module.
"""

from pathlib import Path

from remotezip import RemoteZip

# Zenodo record IDs and ZIP filenames per LU class
_SOURCES = {
    "GL-owl": {
        "record_id": "14137284",
        "zips": {
            "area": "GL-owl_area.zip",
            "HANPPharv": "GL-owl_HANPPharv.zip",
            "NPPeco": "GL-owl_NPPeco.zip",
        },
    },
    "GL-notrees": {
        "record_id": "14013964",
        "zips": {
            "area": "GL-notrees.zip",
            "HANPPharv": "GL-notrees.zip",
            "NPPeco": "GL-notrees.zip",
        },
    },
}

# Units used in inner filenames
_VARIABLE_UNITS = {
    "area": "km2",
    "HANPPharv": "tC",
    "NPPeco": "tC",
}

# Special suffixes for certain (lu_class, variable) combinations.
# GL-owl HANPPharv ZIP contains separate "grazing" and "wood" layers.
_SUFFIX_OVERRIDES = {
    ("GL-owl", "HANPPharv"): "GL-owlgrazing",
}


def _zenodo_url(record_id: str, zip_filename: str) -> str:
    return f"https://zenodo.org/records/{record_id}/files/{zip_filename}"


def _inner_filename(year: str, variable: str, lu_class: str) -> str:
    """Construct the inner GeoTIFF filename within the ZIP archive.

    Files are stored in subdirectories named after the variable.
    """
    unit = _VARIABLE_UNITS[variable]
    name_suffix = _SUFFIX_OVERRIDES.get((lu_class, variable), lu_class)
    return f"{variable}/{year}{variable}_{unit}_{name_suffix}.tif"


def download(
    lu_class: str,
    variable: str,
    year: str,
    output_path: Path,
) -> None:
    """Extract a single GeoTIFF from a Zenodo ZIP via HTTP range requests."""
    source = _SOURCES[lu_class]
    record_id = source["record_id"]
    zip_filename = source["zips"][variable]
    url = _zenodo_url(record_id, zip_filename)
    inner_name = _inner_filename(year, variable, lu_class)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with RemoteZip(url) as rz:
        data = rz.read(inner_name)
    output_path.write_bytes(data)


if __name__ == "__main__":
    download(
        lu_class=snakemake.params.lu_class,  # type: ignore[name-defined]
        variable=snakemake.params.variable,  # type: ignore[name-defined]
        year=snakemake.params.year,  # type: ignore[name-defined]
        output_path=Path(snakemake.output[0]),  # type: ignore[name-defined]
    )
