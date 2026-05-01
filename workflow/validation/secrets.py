# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Load API credentials from secrets file or environment variables.

Credentials are required only for the data-retrieval rules that actually fire,
so the keys we demand depend on the active configuration:

  - USDA: required only when ``data.usda.retrieve_nutrition`` is true.
    The default config ships pre-fetched ``data/curated/nutrition.csv`` (CC0)
    and does not need an API key.
  - ECMWF: required only when ``data_bundle.enabled`` is false. When the
    Zenodo bundle is enabled the resampled Copernicus land cover comes from
    the bundle, so the user does not need to register with the CDS.
"""

import os
from pathlib import Path

import yaml


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _required_credentials(config: dict) -> dict[str, list[str]]:
    """Return service → list of required keys, conditional on the active config."""
    usda_cfg = (
        config.get("data", {}).get("usda", {}) if isinstance(config, dict) else {}
    )
    bundle_cfg = config.get("data_bundle", {}) if isinstance(config, dict) else {}
    needs_usda = bool(usda_cfg.get("retrieve_nutrition", False))
    bundle_enabled = bool(bundle_cfg.get("enabled", False))
    return {
        "usda": ["api_key"] if needs_usda else [],
        "ecmwf": ["url", "key"] if not bundle_enabled else [],
    }


def load_secrets_with_env_fallback(
    project_root: Path, config: dict | None = None
) -> dict:
    """Load API credentials from secrets file or environment variables.

    Environment variables take precedence over the secrets file.

    Parameters
    ----------
    project_root
        Root directory of the repository (used to locate config/secrets.yaml).
    config
        Active Snakemake config dict. Used to determine which credentials are
        actually required. When None, the merged default config is loaded from
        ``config/default.yaml`` and used to make that determination.

    Returns
    -------
    dict
        ``{"usda": {"api_key": str}, "ecmwf": {"url": str, "key": str}}``;
        unused services may have empty inner dicts.

    Raises
    ------
    ValueError
        If any credentials required by the active config are missing.
    """
    if config is None:
        config = _load_yaml(project_root / "config" / "default.yaml")

    credentials: dict[str, dict] = {"usda": {}, "ecmwf": {}}

    # Environment variables (highest priority)
    if usda_key := os.getenv("USDA_API_KEY"):
        credentials["usda"]["api_key"] = usda_key
    if ecmwf_url := os.getenv("ECMWF_DATASTORES_URL"):
        credentials["ecmwf"]["url"] = ecmwf_url
    if ecmwf_key := os.getenv("ECMWF_DATASTORES_KEY"):
        credentials["ecmwf"]["key"] = ecmwf_key

    # Secrets file (fallback)
    secrets_file = project_root / "config" / "secrets.yaml"
    if secrets_file.exists():
        with open(secrets_file) as f:
            file_secrets = yaml.safe_load(f) or {}
        for service in ("usda", "ecmwf"):
            for key, value in (
                file_secrets.get("credentials", {}).get(service, {}) or {}
            ).items():
                credentials[service].setdefault(key, value)

    # Validate only the credentials actually needed by this run
    required = _required_credentials(config)
    missing: list[str] = []
    for service, keys in required.items():
        for key in keys:
            if not credentials.get(service, {}).get(key):
                env_hint = (
                    "USDA_API_KEY"
                    if service == "usda"
                    else f"ECMWF_DATASTORES_{key.upper()}"
                )
                missing.append(
                    f"{service}.{key} (set {env_hint} env var or add to config/secrets.yaml)"
                )

    if missing:
        bundle_enabled = bool(config.get("data_bundle", {}).get("enabled", False))
        bundle_hint = (
            ""
            if bundle_enabled
            else (
                "\nTip: enable the Zenodo data bundle (data_bundle.enabled: true) "
                "to skip Copernicus retrieval and avoid the ECMWF key requirement.\n"
            )
        )
        usda_hint = (
            ""
            if not bool(
                config.get("data", {}).get("usda", {}).get("retrieve_nutrition", False)
            )
            else (
                "\nTip: set data.usda.retrieve_nutrition: false to use the in-tree "
                "nutrition.csv file (CC0) and avoid the USDA key requirement.\n"
            )
        )
        error_msg = f"""
ERROR: Missing API credentials required for data retrieval.

Configure credentials using ONE of these methods:

Option 1 - Secrets file (recommended for local development):
  cp config/secrets.yaml.example config/secrets.yaml
  # then edit config/secrets.yaml

Option 2 - Environment variables (recommended for CI/CD):
  export USDA_API_KEY="your-usda-key"
  export ECMWF_DATASTORES_URL="https://cds.climate.copernicus.eu/api"
  export ECMWF_DATASTORES_KEY="your-ecmwf-key"

Missing credentials:
{chr(10).join('  - ' + m for m in missing)}
{bundle_hint}{usda_hint}
Get API keys:
  - USDA: https://fdc.nal.usda.gov/api-guide.html
  - ECMWF: https://cds.climate.copernicus.eu/api-how-to
"""
        raise ValueError(error_msg)

    return credentials
