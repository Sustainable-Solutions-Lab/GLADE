# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for multi-cropping combinations and the MIRCA-OS crop concordance."""

from pathlib import Path

import pandas as pd
from snakemake.logging import logger

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

# Crops in the agronomic seed set of the Stage-1 derivation. These must be
# mapped (non-blank) for the seeded combinations to be derivable at all.
REQUIRED_MAPPED_MIRCA_CROPS = frozenset(
    {"Rice", "Wheat", "Maize", "Soybeans", "Cotton"}
)


def validate_multi_cropping(config: dict, project_root: Path) -> None:
    """Validate multi-cropping combinations and the MIRCA-OS crop concordance.

    Two invariants:

    1. Every crop referenced by a ``config["multiple_cropping"]`` combination is
       in ``config["crops"]`` (so its crop buses exist at build time).
    2. ``data/curated/mirca_os_crop_mapping.csv`` is exhaustive over the 23
       MIRCA-OS base crops, every non-blank ``glade_crop`` is in
       ``config["crops"]``, and the agronomic seed-set crops are mapped. This
       mirrors ``validate_cropgrids_crops`` for the CROPGRIDS concordance.
    """
    config_crops = set(config["crops"])

    combinations = config.get("multiple_cropping")
    if combinations:
        missing: list[str] = []
        for combo_name, entry in combinations.items():
            if entry is None:
                continue
            for crop in entry["crops"]:
                if crop not in config_crops:
                    missing.append(f"{combo_name}: {crop}")
        if missing:
            detail = ", ".join(missing)
            raise ValueError(
                "Multi-cropping combinations reference crops not in "
                f"config['crops']: {detail}"
            )

    _validate_mirca_concordance(config_crops, project_root)


def _validate_mirca_concordance(config_crops: set[str], project_root: Path) -> None:
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

    unmapped_required = sorted(
        crop for crop in REQUIRED_MAPPED_MIRCA_CROPS if glade.get(crop, "") == ""
    )
    if unmapped_required:
        raise ValueError(
            "mirca_os_crop_mapping.csv leaves seed-set crops unmapped: "
            f"{unmapped_required}"
        )
