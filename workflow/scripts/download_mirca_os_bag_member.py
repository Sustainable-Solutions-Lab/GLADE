"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Download a single member of the MIRCA-OS v2 HydroShare BagIt archive.

HydroShare's per-file iRODS endpoint (`.../resource/{id}/data/contents/{file}`)
returns HTTP 500 for this resource, so the only reliable path is the
whole-resource BagIt zip endpoint:

    https://www.hydroshare.org/django_irods/download/bags/{resource}.zip

which 302-redirects to a range-capable *signed* S3 URL (a HEAD 403s -- the
signature is GET-only). We resolve that redirect, then use ``remotezip`` to read
the bag's central directory and pull only the one requested member via HTTP range
requests, so a 32 MB grid archive does not require downloading the full ~10 GB bag.

The bag nests the grid archives as RAR5 files (extracted downstream with
``bsdtar``); the 2020 crop-calendar CSVs are stored directly.
"""

from pathlib import Path

import remotezip
import requests

MIRCA_OS_V2_RESOURCE = "e4582ca0042148338bb5e0148b749ed6"
BAG_URL = (
    "https://www.hydroshare.org/django_irods/download/bags/"
    f"{MIRCA_OS_V2_RESOURCE}.zip"
)


def resolve_signed_url() -> str:
    """Resolve the bag endpoint's 302 redirect to the signed S3 URL."""
    resp = requests.get(BAG_URL, allow_redirects=False, timeout=120)
    location = resp.headers.get("Location")
    if resp.status_code != 302 or not location:
        raise RuntimeError(
            f"Expected a 302 redirect from {BAG_URL}, got HTTP {resp.status_code}"
        )
    return location


def main() -> None:
    member = str(snakemake.params.member)  # type: ignore[name-defined]
    output = Path(snakemake.output[0])  # type: ignore[name-defined]
    output.parent.mkdir(parents=True, exist_ok=True)

    signed_url = resolve_signed_url()
    member_path = f"{MIRCA_OS_V2_RESOURCE}/data/contents/{member}"

    with remotezip.RemoteZip(signed_url) as bag:
        names = set(bag.namelist())
        if member_path not in names:
            raise KeyError(
                f"Member '{member_path}' not found in MIRCA-OS v2 bag "
                f"({len(names)} members)"
            )
        data = bag.read(member_path)

    tmp = output.with_suffix(output.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(output)
    print(f"Downloaded {len(data):,} bytes -> {output}")


main()
