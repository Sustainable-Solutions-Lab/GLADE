# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resolve the curated and manually configured multi-cropping systems."""

from pathlib import Path

import yaml

MIRCA_MULTICROPPING_YEARS = (2010, 2015, 2020)


def closest_mirca_multicropping_year(baseline_year: int) -> int:
    """Select the nearest supported MIRCA release, preferring the earlier tie."""
    return min(
        MIRCA_MULTICROPPING_YEARS,
        key=lambda year: (abs(year - baseline_year), year),
    )


def load_catalog_combinations(catalog_yaml: str | Path) -> dict[str, dict]:
    """Load the fixed MIRCA-OS combination catalog."""
    with open(catalog_yaml) as f:
        catalog = yaml.safe_load(f) or {}
    if not isinstance(catalog, dict):
        raise ValueError("The MIRCA-OS multi-cropping catalog must be a mapping")
    for name, entry in catalog.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Catalog combination names must be non-empty strings")
        if not isinstance(entry, dict):
            raise ValueError(f"Catalog combination '{name}' must be a mapping")
        if set(entry) != {"crops", "water_supplies"}:
            raise ValueError(
                f"Catalog combination '{name}' must contain only crops and "
                "water_supplies"
            )
        crops = entry.get("crops")
        supplies = entry.get("water_supplies")
        if (
            not isinstance(crops, list)
            or len(crops) not in {2, 3}
            or any(not isinstance(crop, str) or not crop for crop in crops)
        ):
            raise ValueError(f"Catalog combination '{name}' must contain 2 or 3 crops")
        if (
            not isinstance(supplies, list)
            or not supplies
            or len(supplies) != len(set(supplies))
        ):
            raise ValueError(
                f"Catalog combination '{name}' must contain unique water_supplies"
            )
        if set(supplies) - {"r", "i"}:
            raise ValueError(
                f"Catalog combination '{name}' has invalid water supplies: {supplies}"
            )
    return catalog


def _resolved_sets(
    config: dict, catalog_yaml: str | Path
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return ``(effective, observed)`` combination mappings.

    Catalog entries carry a MIRCA-observed baseline. Configuration may disable
    one by setting its name to ``null`` or add a uniquely named greenfield
    system, whose baseline is zero. Redefining a catalog entry is rejected: its
    name, crop sequence, observed anchor, and calibration key form one structural
    identity.
    """
    catalog = load_catalog_combinations(catalog_yaml)
    observed = dict(catalog)
    greenfield: dict[str, dict] = {}

    for name, entry in config["multiple_cropping"].items():
        if name in catalog:
            if entry is not None:
                raise ValueError(
                    f"multiple_cropping.{name} redefines a curated MIRCA-OS "
                    "combination. Use null to disable it or add a uniquely named "
                    "greenfield combination."
                )
            observed.pop(name)
            continue
        if entry is None:
            raise ValueError(
                f"multiple_cropping.{name} is null but is not a curated "
                "combination and therefore cannot disable anything"
            )
        greenfield[name] = entry

    model_crops = set(config["crops"])
    observed = {
        name: entry
        for name, entry in observed.items()
        if set(entry["crops"]) <= model_crops
    }
    greenfield = {
        name: entry
        for name, entry in greenfield.items()
        if set(entry["crops"]) <= model_crops
    }
    return {**observed, **greenfield}, observed


def effective_combinations(config: dict, catalog_yaml: str | Path) -> dict[str, dict]:
    """Return all modeled combinations: observed catalog plus greenfield config."""
    effective, _observed = _resolved_sets(config, catalog_yaml)
    return effective


def observed_combinations(config: dict, catalog_yaml: str | Path) -> dict[str, dict]:
    """Return enabled catalog combinations that carry a MIRCA baseline."""
    _effective, observed = _resolved_sets(config, catalog_yaml)
    return observed
