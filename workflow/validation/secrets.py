# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Load API credentials from secrets file or environment variables."""

import os
from pathlib import Path

import yaml


def load_secrets_with_env_fallback(project_root: Path) -> dict:
    """Load build-time API credentials from secrets file or environment variables.

    Environment variables take precedence over the secrets file. This allows
    overriding file-based credentials in CI/CD or testing environments.

    The only build-time credential is the USDA FoodData Central key, used by
    the retrieve_usda_nutrition rule (data.usda.retrieve_nutrition: true) when
    refreshing nutrition data. That rule validates the key's presence itself,
    so this loader just gathers whatever credentials are configured. Copernicus
    CDS credentials are maintainer-only (tools/mirror_land_cover.py refreshes
    the land-cover data mirrored on Zenodo) and are not loaded here.

    Environment variables:
        USDA_API_KEY: USDA FoodData Central API key

    Parameters
    ----------
    project_root
        Root directory of the repository (used to locate config/secrets.yaml).

    Returns
    -------
    dict
        Credentials structure ``{"usda": {"api_key": str}}``. ``api_key`` is
        omitted when no key is configured.
    """
    credentials = {"usda": {}}

    # Check environment variables first (highest priority)
    if usda_key := os.getenv("USDA_API_KEY"):
        credentials["usda"]["api_key"] = usda_key

    # Try secrets file as fallback
    secrets_file = project_root / "config" / "secrets.yaml"
    if secrets_file.exists():
        with open(secrets_file) as f:
            file_secrets = yaml.safe_load(f)

        # Merge file secrets (env vars take precedence)
        if file_secrets and "credentials" in file_secrets:
            for service in ["usda"]:
                if service in file_secrets["credentials"]:
                    for key, value in file_secrets["credentials"][service].items():
                        credentials[service].setdefault(key, value)

    return credentials
