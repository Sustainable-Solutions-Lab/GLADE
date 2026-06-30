# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mirror the Copernicus ESA CCI land-cover map onto Zenodo.

This is a MAINTAINER tool, not part of the build. It:

1. downloads the ``satellite-land-cover`` dataset for a given year/version from
   the Copernicus Climate Data Store (needs an ECMWF/CDS token),
2. extracts the ``lccs_class`` variable (the only one the model uses), and
3. uploads the result to Zenodo under CC-BY-4.0 with the Copernicus attribution
   required by the dataset licence.

Ordinary builds then fetch the file from Zenodo with no API key (see the
``download_land_cover`` rule and ``config['data']['land_cover']['zenodo_record']``).

The 2016-onwards C3S land-cover maps are licensed CC-BY-4.0, which permits this
redistribution provided the Copernicus attribution and source DOI are retained;
both are embedded in the Zenodo deposition metadata below.

Run inside the project environment, e.g.::

    # First publication (creates a new Zenodo record):
    pixi run -e dev python tools/mirror_land_cover.py

    # Refresh / new data version (new version of an existing record):
    pixi run -e dev python tools/mirror_land_cover.py --parent-record 1234567

    # Dry-run against the Zenodo sandbox, leaving an unpublished draft:
    pixi run -e dev python tools/mirror_land_cover.py --sandbox --no-publish

Credentials are read from environment variables (``ECMWF_DATASTORES_URL`` /
``ECMWF_DATASTORES_KEY`` / ``ZENODO_TOKEN``) or from ``config/secrets.yaml``
(``credentials.ecmwf`` and ``credentials.zenodo``).

After publishing, set ``config['data']['land_cover']['zenodo_record']`` in
``config/default.yaml`` to the printed record id.
"""

import argparse
import os
from pathlib import Path
import sys

import yaml

# Source DOI of the Copernicus CDS land-cover dataset (all versions/years).
SOURCE_DOI = "10.24381/cds.006f2c9a"

# Attribution wording required by the Copernicus product licence (CC-BY-4.0).
COPERNICUS_ATTRIBUTION = (
    "Generated using Copernicus Climate Change Service information {year}. "
    "Neither the European Commission nor ECMWF is responsible for any use that "
    "may be made of the Copernicus information or data it contains."
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_secret(env_var: str, *yaml_path: str) -> str | None:
    """Return a secret from an env var, falling back to config/secrets.yaml.

    ``yaml_path`` is the nested key path under the top-level ``credentials``
    mapping, e.g. ``("ecmwf", "key")``.
    """
    if value := os.getenv(env_var):
        return value

    secrets_file = PROJECT_ROOT / "config" / "secrets.yaml"
    if not secrets_file.exists():
        return None
    with open(secrets_file) as handle:
        secrets = yaml.safe_load(handle) or {}
    node = secrets.get("credentials", {})
    for key in yaml_path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


def _default_year_and_version() -> tuple[int, str]:
    """Read the baseline year and land-cover version from config/default.yaml."""
    with open(PROJECT_ROOT / "config" / "default.yaml") as handle:
        config = yaml.safe_load(handle)
    return config["baseline_year"], config["data"]["land_cover"]["version"]


def _build_metadata(year: int, version: str) -> dict:
    """Zenodo deposition metadata carrying the required Copernicus attribution."""
    attribution = COPERNICUS_ATTRIBUTION.format(year=year)
    description = (
        f"<p>Land cover classification (<code>lccs_class</code> variable only) "
        f"for {year}, Copernicus ESA CCI land cover version {version}, at 300 m "
        f"global resolution. Extracted from the Copernicus Climate Data Store "
        f"<code>satellite-land-cover</code> dataset and redistributed here so "
        f"that the GLADE model can be built without a Copernicus CDS API key.</p>"
        f"<p>This is a mirror of a third-party product, provided under the "
        f"Creative Commons Attribution 4.0 International licence (CC-BY-4.0).</p>"
        f"<p><strong>Attribution:</strong> {attribution}</p>"
        f"<p><strong>Source:</strong> Copernicus Climate Change Service, Climate "
        f"Data Store (2019): Land cover classification gridded maps from 1992 to "
        f"present derived from satellite observation. "
        f"DOI: https://doi.org/{SOURCE_DOI}</p>"
    )
    return {
        "title": (
            f"Copernicus ESA CCI land cover (lccs_class), {year}, "
            f"{version} - GLADE mirror"
        ),
        "upload_type": "dataset",
        "description": description,
        "creators": [
            {"name": "Copernicus Climate Change Service (C3S)"},
            {"name": "UCLouvain"},
        ],
        "license": "cc-by-4.0",
        "access_right": "open",
        "keywords": ["land cover", "ESA CCI", "Copernicus", "C3S", "GLADE"],
        "related_identifiers": [
            {
                "identifier": SOURCE_DOI,
                "relation": "isDerivedFrom",
                "scheme": "doi",
                "resource_type": "dataset",
            }
        ],
        "notes": attribution,
    }


def main() -> None:
    default_year, default_version = _default_year_and_version()

    parser = argparse.ArgumentParser(
        description="Mirror the Copernicus ESA CCI land-cover map onto Zenodo.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--year", type=int, default=default_year, help="Land-cover map year."
    )
    parser.add_argument(
        "--version", default=default_version, help="ESA CCI land-cover version."
    )
    parser.add_argument(
        "--parent-record",
        default=None,
        help="Existing Zenodo record id to publish a new version of (refresh).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "downloads",
        help="Directory for the downloaded archive and extracted NetCDF.",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Use sandbox.zenodo.org instead of the production service.",
    )
    parser.add_argument(
        "--no-publish",
        dest="publish",
        action="store_false",
        help="Leave the Zenodo deposition as an unpublished draft for review.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Reuse an existing extracted NetCDF in --work-dir (skip the CDS download).",
    )
    parser.add_argument(
        "--publish-record",
        default=None,
        help=(
            "Publish an existing draft deposition by record id (e.g. after "
            "reviewing a draft created with --no-publish) and exit. Irreversible."
        ),
    )
    args = parser.parse_args()

    # Make the workflow scripts and sibling tools importable.
    sys.path.insert(0, str(PROJECT_ROOT / "workflow" / "scripts"))
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import download_land_cover
    import extract_land_cover_class
    from zenodo_publish import publish_dataset, publish_draft

    if args.publish_record:
        zenodo_token = _load_secret("ZENODO_TOKEN", "zenodo", "token")
        if not zenodo_token:
            parser.error(
                "Missing Zenodo token. Set ZENODO_TOKEN, or credentials.zenodo.token "
                "in config/secrets.yaml."
            )
        result = publish_draft(zenodo_token, args.publish_record, sandbox=args.sandbox)
        print("Published.")
        print(f"  record id : {result['record_id']}")
        print(f"  doi       : {result['doi']}")
        print(
            "\nSet this in config/default.yaml under data.land_cover:\n"
            f'    zenodo_record: "{result["record_id"]}"'
        )
        return

    args.work_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"land_cover_lccs_class_{args.year}_{args.version}.nc"
    extracted = args.work_dir / target_name

    if args.skip_download:
        if not extracted.exists():
            parser.error(f"--skip-download given but {extracted} does not exist")
        print(f"Reusing existing {extracted}")
    else:
        ecmwf_url = _load_secret("ECMWF_DATASTORES_URL", "ecmwf", "url")
        ecmwf_key = _load_secret("ECMWF_DATASTORES_KEY", "ecmwf", "key")
        if not ecmwf_url or not ecmwf_key:
            parser.error(
                "Missing Copernicus CDS credentials. Set ECMWF_DATASTORES_URL and "
                "ECMWF_DATASTORES_KEY, or credentials.ecmwf.{url,key} in "
                "config/secrets.yaml."
            )

        archive = args.work_dir / f"land_cover_{args.year}_{args.version}.zip"
        print(f"Downloading satellite-land-cover {args.version} for {args.year} ...")
        download_land_cover.main(
            dataset="satellite-land-cover",
            request={
                "variable": "all",
                "year": [str(args.year)],
                "version": [args.version],
            },
            output=archive,
            url=ecmwf_url,
            key=ecmwf_key,
        )
        print(f"Extracting lccs_class -> {extracted} ...")
        extract_land_cover_class.main(input_path=archive, output_path=extracted)
        archive.unlink(missing_ok=True)

    zenodo_token = _load_secret("ZENODO_TOKEN", "zenodo", "token")
    if not zenodo_token:
        parser.error(
            "Missing Zenodo token. Set ZENODO_TOKEN, or credentials.zenodo.token "
            "in config/secrets.yaml."
        )

    print("Publishing to Zenodo ...")
    result = publish_dataset(
        token=zenodo_token,
        files=[extracted],
        metadata=_build_metadata(args.year, args.version),
        parent_record=args.parent_record,
        sandbox=args.sandbox,
        publish=args.publish,
    )

    print("\nDone.")
    print(f"  record id : {result['record_id']}")
    print(f"  doi       : {result['doi']}")
    if result["draft"]:
        edit_link = result["links"].get("html") or result["links"].get("latest_draft")
        print(f"  status    : DRAFT (not published) - review at {edit_link}")
    else:
        print(
            "\nSet this in config/default.yaml under data.land_cover:\n"
            f'    zenodo_record: "{result["record_id"]}"'
        )


if __name__ == "__main__":
    main()
