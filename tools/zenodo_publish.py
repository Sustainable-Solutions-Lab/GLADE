# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reusable Zenodo REST API publisher for redistributing input datasets.

This module is dataset-agnostic: it creates (or versions) a Zenodo deposition,
uploads one or more files, sets metadata, and optionally publishes. Use it for
any dataset whose licence permits redistribution but whose original source is
behind an API key or registration wall, so that ordinary builds can fetch a
mirror with a plain HTTP download.

The first consumer is tools/mirror_land_cover.py. See docs/data_sources.rst for
the overall mirroring workflow.

Requires a Zenodo personal access token with the ``deposit:write`` and
``deposit:actions`` scopes (create one at
https://zenodo.org/account/settings/applications/tokens/new/).
"""

from pathlib import Path

import requests

ZENODO_BASE = "https://zenodo.org"
ZENODO_SANDBOX_BASE = "https://sandbox.zenodo.org"


def _raise_for_status(response: requests.Response) -> None:
    """Raise a descriptive error including the Zenodo response body."""
    if not response.ok:
        raise RuntimeError(
            f"Zenodo API error {response.status_code} for "
            f"{response.request.method} {response.url}: {response.text}"
        )


def publish_draft(token: str, record_id: str, *, sandbox: bool = False) -> dict:
    """Publish an existing draft deposition (e.g. after manual review).

    This finalizes a draft created earlier with ``publish=False`` without
    re-uploading anything. Publishing is irreversible.

    Returns ``{"record_id": str, "doi": str | None, "draft": False, "links": dict}``.
    """
    base = ZENODO_SANDBOX_BASE if sandbox else ZENODO_BASE
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    response = session.post(
        f"{base}/api/deposit/depositions/{record_id}/actions/publish"
    )
    _raise_for_status(response)
    deposition = response.json()
    return {
        "record_id": str(deposition["id"]),
        "doi": deposition.get("doi"),
        "draft": False,
        "links": deposition.get("links", {}),
    }


def publish_dataset(
    token: str,
    files: list[Path],
    metadata: dict,
    *,
    parent_record: str | None = None,
    sandbox: bool = False,
    publish: bool = True,
) -> dict:
    """Create or version a Zenodo deposition, upload files, set metadata, publish.

    Parameters
    ----------
    token
        Zenodo personal access token (deposit:write + deposit:actions scopes).
    files
        Files to upload as the deposition content. When versioning, these
        replace any files inherited from the previous version.
    metadata
        Zenodo deposition metadata (the value of the API "metadata" key), e.g.
        title, upload_type, description, creators, license, access_right.
    parent_record
        If given, create a new version of this existing published record id
        (used to refresh a mirror). Otherwise create a brand-new deposition.
    sandbox
        Target sandbox.zenodo.org instead of the production service.
    publish
        Publish the deposition (irreversible). If False, leave it as a draft
        for manual review in the Zenodo web UI.

    Returns
    -------
    dict
        ``{"record_id": str, "doi": str | None, "draft": bool, "links": dict}``.
    """
    base = ZENODO_SANDBOX_BASE if sandbox else ZENODO_BASE
    api = f"{base}/api"

    # Authenticate via a bearer header rather than a query parameter so the
    # token never ends up in a URL, error message, or log line.
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    json_headers = {"Content-Type": "application/json"}

    if parent_record:
        # Open a new-version draft from the latest published record.
        response = session.post(
            f"{api}/deposit/depositions/{parent_record}/actions/newversion",
        )
        _raise_for_status(response)
        latest_draft_url = response.json()["links"]["latest_draft"]
        response = session.get(latest_draft_url)
        _raise_for_status(response)
        deposition = response.json()
    else:
        response = session.post(
            f"{api}/deposit/depositions", json={}, headers=json_headers
        )
        _raise_for_status(response)
        deposition = response.json()

    deposition_id = deposition["id"]
    bucket_url = deposition["links"]["bucket"]

    # When versioning, drop files inherited from the previous version so the new
    # version contains exactly `files`.
    for existing in deposition.get("files", []):
        response = session.delete(
            f"{api}/deposit/depositions/{deposition_id}/files/{existing['id']}",
        )
        _raise_for_status(response)

    # Upload via the bucket API (streams large files without multipart overhead).
    for file_path in files:
        file_path = Path(file_path)
        with open(file_path, "rb") as handle:
            response = session.put(f"{bucket_url}/{file_path.name}", data=handle)
            _raise_for_status(response)

    # Attach metadata.
    response = session.put(
        f"{api}/deposit/depositions/{deposition_id}",
        headers=json_headers,
        json={"metadata": metadata},
    )
    _raise_for_status(response)
    deposition = response.json()

    prereserved = deposition.get("metadata", {}).get("prereserved_doi")
    doi = prereserved.get("doi") if isinstance(prereserved, dict) else None

    if publish:
        response = session.post(
            f"{api}/deposit/depositions/{deposition_id}/actions/publish"
        )
        _raise_for_status(response)
        deposition = response.json()
        doi = deposition.get("doi", doi)

    return {
        "record_id": str(deposition_id),
        "doi": doi,
        "draft": not publish,
        "links": deposition.get("links", {}),
    }
