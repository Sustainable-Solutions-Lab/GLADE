# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for the optional data bundle and inter-config consistency.

Two checks live here:

1. Bundle compatibility — when `data_bundle.enabled: true`, the user's
   `countries`, `food_groups.included`, `health.causes`, `health.risk_factors`,
   and `baseline_year` must be subsets of (or equal to) the values recorded
   in the bundle's manifest.yaml. We also enforce `baseline_year == 2020`.

2. Country superset across configs — `default.yaml`'s `countries` is the
   canonical superset; raise if any other tracked config asks for a country
   not in the default list. Runs irrespective of bundle status.
"""

from pathlib import Path

import yaml


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _check_bundle_compatibility(config: dict, project_root: Path) -> None:
    bundle_cfg = config["data_bundle"]
    if not bundle_cfg["enabled"]:
        return

    if config["baseline_year"] != 2020:
        raise ValueError(
            "data_bundle.enabled is true but baseline_year != 2020. "
            "The published bundle is only valid for baseline_year: 2020. "
            "Either set baseline_year: 2020 or disable the bundle."
        )

    destination = Path(bundle_cfg["destination"])
    if not destination.is_absolute():
        destination = project_root / destination
    manifest_path = destination / "manifest.yaml"
    if not manifest_path.exists():
        # Bundle has not yet been downloaded; download_data_bundle handles that.
        # Nothing to compare against here.
        return

    manifest = _load_yaml(manifest_path)
    compat = manifest.get("compatibility", {})

    user_countries = set(config["countries"])
    bundle_countries = set(compat.get("countries", []))
    extra_countries = sorted(user_countries - bundle_countries)
    if extra_countries:
        raise ValueError(
            f"data_bundle.enabled is true but config.countries includes "
            f"{len(extra_countries)} entries not covered by the bundle "
            f"(version {manifest.get('version')}): "
            f"{extra_countries[:10]}{'...' if len(extra_countries) > 10 else ''}. "
            "Either restrict countries to the bundle coverage or disable the bundle."
        )

    for key, label in [
        (("food_groups", "included"), "food_groups.included"),
        (("health", "causes"), "health.causes"),
        (("health", "risk_factors"), "health.risk_factors"),
    ]:
        user_set = set(config[key[0]][key[1]])
        bundle_key = (
            "food_groups" if label == "food_groups.included" else label.split(".")[1]
        )
        bundle_set = set(compat.get(bundle_key, []))
        extra = sorted(user_set - bundle_set)
        if extra:
            raise ValueError(
                f"data_bundle.enabled is true but config.{label} includes "
                f"entries not covered by the bundle (version {manifest.get('version')}): "
                f"{extra}. Either restrict {label} to the bundle coverage or "
                "disable the bundle."
            )


def _check_country_superset(config: dict, project_root: Path) -> None:
    """Raise if any tracked config asks for countries not in default.yaml."""
    default_path = project_root / "config" / "default.yaml"
    if not default_path.exists():
        return
    default_countries = set(_load_yaml(default_path).get("countries", []))
    if not default_countries:
        return

    user_countries = set(config["countries"])
    extra = sorted(user_countries - default_countries)
    if extra:
        raise ValueError(
            "config.countries contains ISO3 codes not present in "
            "config/default.yaml's country list, which is intended to be the "
            f"canonical superset: {extra}. Either add them to default.yaml or "
            "remove them from this config."
        )


def validate_data_bundle(config: dict, project_root: Path) -> None:
    """Run both bundle compatibility and country-superset checks."""
    _check_country_superset(config, project_root)
    _check_bundle_compatibility(config, project_root)
