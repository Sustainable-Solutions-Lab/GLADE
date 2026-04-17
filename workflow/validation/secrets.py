# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Load API credentials from secrets file or environment variables."""

import os
from pathlib import Path

import yaml


def load_secrets_with_env_fallback(project_root: Path) -> dict:
    """Load API credentials from secrets file or environment variables.

    Environment variables take precedence over the secrets file. This allows
    overriding file-based credentials in CI/CD or testing environments.

    Environment variables:
        USDA_API_KEY: USDA FoodData Central API key
        ECMWF_DATASTORES_URL: ECMWF datastores API URL
        ECMWF_DATASTORES_KEY: ECMWF datastores API key

    Parameters
    ----------
    project_root
        Root directory of the repository (used to locate config/secrets.yaml).

    Returns
    -------
    dict
        Dictionary with credentials structure:
        {
            "usda": {"api_key": str},
            "ecmwf": {"url": str, "key": str}
        }

    Raises
    ------
    ValueError
        If any required credentials are missing from both environment variables
        and the secrets file.
    """
    credentials = {"usda": {}, "ecmwf": {}}

    # Check environment variables first (highest priority)
    if usda_key := os.getenv("USDA_API_KEY"):
        credentials["usda"]["api_key"] = usda_key

    if ecmwf_url := os.getenv("ECMWF_DATASTORES_URL"):
        credentials["ecmwf"]["url"] = ecmwf_url

    if ecmwf_key := os.getenv("ECMWF_DATASTORES_KEY"):
        credentials["ecmwf"]["key"] = ecmwf_key

    # Try secrets file as fallback
    secrets_file = project_root / "config" / "secrets.yaml"
    if secrets_file.exists():
        with open(secrets_file) as f:
            file_secrets = yaml.safe_load(f)

        # Merge file secrets (env vars take precedence)
        if file_secrets and "credentials" in file_secrets:
            for service in ["usda", "ecmwf"]:
                if service in file_secrets["credentials"]:
                    for key, value in file_secrets["credentials"][service].items():
                        credentials[service].setdefault(key, value)

    # Validate all required credentials are present
    missing = []
    if not credentials["usda"].get("api_key"):
        missing.append(
            "USDA API key (set USDA_API_KEY env var or add to config/secrets.yaml)"
        )
    if not credentials["ecmwf"].get("url"):
        missing.append(
            "ECMWF URL (set ECMWF_DATASTORES_URL env var or add to config/secrets.yaml)"
        )
    if not credentials["ecmwf"].get("key"):
        missing.append(
            "ECMWF key (set ECMWF_DATASTORES_KEY env var or add to config/secrets.yaml)"
        )

    if missing:
        error_msg = f"""
ERROR: Missing API credentials required for data retrieval.

Please configure credentials using ONE of these methods:

Option 1 - Secrets file (recommended for local development):
  1. Copy config/secrets.yaml.example to config/secrets.yaml:
     cp config/secrets.yaml.example config/secrets.yaml
  2. Edit config/secrets.yaml and fill in your API credentials

Option 2 - Environment variables (recommended for CI/CD):
  export USDA_API_KEY="your-usda-key"
  export ECMWF_DATASTORES_URL="https://cds.climate.copernicus.eu/api"
  export ECMWF_DATASTORES_KEY="your-ecmwf-key"

Missing credentials:
{chr(10).join('  - ' + m for m in missing)}

Get API keys:
  - USDA: https://fdc.nal.usda.gov/api-guide.html
  - ECMWF: https://cds.climate.copernicus.eu/api-how-to
"""
        raise ValueError(error_msg)

    return credentials
