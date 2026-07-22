# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for multi-cropping combinations and the MIRCA-OS crop concordance."""

from pathlib import Path

import pandas as pd
from snakemake.logging import logger

from workflow.scripts.multi_cropping_combinations import (
    effective_combinations,
    load_catalog_combinations,
)

# The 23 base crops of MIRCA-OS v2 (Kebede et al. 2025). The concordance file
# must cover exactly this set so the derivation fails fast on any drift.
MIRCA_OS_BASE_CROPS = frozenset(
    {
        "Barley",
        "Cassava",
        "Cocoa",
        "Coffee",
        "Cotton",
        "Fodder",
        "Groundnuts",
        "Maize",
        "Millet",
        "Oil palm",
        "Others annual",
        "Others perennial",
        "Potatoes",
        "Pulses",
        "Rapeseed",
        "Rice",
        "Rye",
        "Sorghum",
        "Soybeans",
        "Sugar beet",
        "Sugar cane",
        "Sunflower",
        "Wheat",
    }
)

CATALOG_PATH = Path("data/curated/mirca_os_multicropping_combinations.yaml")


def validate_multi_cropping(config: dict, project_root: Path) -> None:
    """Validate multi-cropping combinations and the MIRCA-OS crop concordance.

    Three invariants:

    1. Every crop referenced by a ``config["multiple_cropping"]`` combination is
       in ``config["crops"]`` (so its crop buses exist at build time).
    2. Catalog entries can only be disabled; config-only entries are explicit
       zero-baseline greenfield systems and cannot use catalog names.
    3. ``data/curated/mirca_os_crop_mapping.csv`` is exhaustive over the 23
       MIRCA-OS base crops and maps every crop used by the fixed catalog. This
       mirrors ``validate_cropgrids_crops`` for the CROPGRIDS concordance.
    """
    config_crops = set(config["crops"])

    missing = [
        f"{combo_name}: {crop}"
        for combo_name, entry in config["multiple_cropping"].items()
        if entry is not None
        for crop in entry["crops"]
        if crop not in config_crops
    ]
    if missing:
        raise ValueError(
            "Multi-cropping combinations reference crops not in "
            f"config['crops']: {', '.join(missing)}"
        )

    catalog_path = project_root / CATALOG_PATH
    effective_combinations(config, catalog_path)
    catalog = load_catalog_combinations(catalog_path)
    _validate_mirca_concordance(config_crops, project_root, catalog)


def _validate_mirca_concordance(
    config_crops: set[str], project_root: Path, catalog: dict[str, dict]
) -> None:
    """Check the MIRCA-OS -> GLADE crop concordance file."""
    mapping_path = project_root / "data" / "curated" / "mirca_os_crop_mapping.csv"
    if not mapping_path.exists():
        raise FileNotFoundError(f"Expected data file at {mapping_path}")

    mapping = pd.read_csv(mapping_path, comment="#")
    required_cols = {"mirca_crop", "glade_crop"}
    missing_cols = required_cols - set(mapping.columns)
    if missing_cols:
        raise ValueError(
            f"mirca_os_crop_mapping.csv missing columns: {sorted(missing_cols)}"
        )

    mapping["mirca_crop"] = mapping["mirca_crop"].astype(str).str.strip()
    mirca_crops = set(mapping["mirca_crop"])

    if len(mapping) != len(mirca_crops):
        dupes = sorted(mapping["mirca_crop"][mapping["mirca_crop"].duplicated()])
        raise ValueError(f"mirca_os_crop_mapping.csv has duplicate mirca_crop: {dupes}")

    unexpected = sorted(mirca_crops - MIRCA_OS_BASE_CROPS)
    if unexpected:
        raise ValueError(
            f"mirca_os_crop_mapping.csv has unknown MIRCA-OS crops: {unexpected}"
        )
    absent = sorted(MIRCA_OS_BASE_CROPS - mirca_crops)
    if absent:
        raise ValueError(
            f"mirca_os_crop_mapping.csv is missing MIRCA-OS base crops: {absent}"
        )

    # glade_crop is blank for dropped crops; NaN reads as an empty mapping.
    glade = mapping.set_index("mirca_crop")["glade_crop"]
    glade = glade.fillna("").astype(str).str.strip()

    # The concordance is a global reference file; a reduced config (e.g. the test
    # config's crop subset) legitimately omits some mapped crops. Warn rather than
    # fail, mirroring validate_gaez_crop_mapping / validate_seed_rates. Combination
    # crops are still hard-validated against config['crops'] above.
    mapped = glade[glade != ""]
    unused = sorted(set(mapped) - config_crops)
    if unused:
        logger.warning(
            "mirca_os_crop_mapping.csv maps to crops not in config "
            f"(future crops?): {', '.join(unused)}"
        )

    catalog_crops = {crop for entry in catalog.values() for crop in entry["crops"]}
    unmapped_catalog = sorted(catalog_crops - set(mapped))
    if unmapped_catalog:
        raise ValueError(
            f"mirca_os_crop_mapping.csv does not map catalog crops: {unmapped_catalog}"
        )
