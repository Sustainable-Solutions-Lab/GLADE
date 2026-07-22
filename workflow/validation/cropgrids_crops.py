# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for the cropgrids_crops list and its cross-array invariants."""

from pathlib import Path

import pandas as pd

from workflow.scripts.multi_cropping_combinations import effective_combinations

CATALOG_PATH = Path("data/curated/mirca_os_multicropping_combinations.yaml")


def validate_cropgrids_crops(config: dict, project_root: Path) -> None:
    """Check cross-array invariants for ``cropgrids_crops``.

    Crops listed under ``cropgrids_crops`` bypass the GAEZ pipeline (yield,
    suitability, water requirement, growing season, harvested area) and are
    sourced from CROPGRIDS + FAOSTAT instead. The following invariants are
    required for the dispatch in ``workflow/rules/crops.smk`` to be coherent:

    1. Every entry appears in ``config["crops"]``.
    2. No entry appears in ``config["irrigation"]["irrigated_crops"]``.
       Listed crops are rainfed-only by construction.
    3. No entry appears in any ``config["multiple_cropping"]`` combination.
       The GAEZ growing-season rasters that drive multi-cropping are absent
       for these crops.
    4. Every entry has a row in ``data/curated/cropgrids_crop_mapping.csv``
       with a non-empty ``cropgrids_name`` (so the CROPGRIDS NetCDF can be
       extracted), a non-empty ``faostat_qcl_item_code`` (so FAOSTAT yield
       can be looked up), and a non-empty ``faostat_qcl_yield_element_code``.
    5. No entry appears in ``data/curated/gaez_crop_code_mapping.csv``: it is
       reserved for GAEZ-backed crops, and a stray entry would otherwise
       feed the GAEZ download rules.
    """
    cropgrids_crops = list(config.get("cropgrids_crops") or [])
    if not cropgrids_crops:
        return

    crops = set(config["crops"])
    missing_from_crops = sorted(set(cropgrids_crops) - crops)
    if missing_from_crops:
        raise ValueError(
            "cropgrids_crops entries not present in crops: "
            f"{', '.join(missing_from_crops)}"
        )

    irrigation_cfg = config["irrigation"]["irrigated_crops"]
    if isinstance(irrigation_cfg, list):
        irrigated_crops = set(irrigation_cfg)
        overlap_irrigation = sorted(set(cropgrids_crops) & irrigated_crops)
        if overlap_irrigation:
            raise ValueError(
                "cropgrids_crops must be rainfed-only; the following also "
                f"appear in irrigation.irrigated_crops: {', '.join(overlap_irrigation)}"
            )

    combos = effective_combinations(config, project_root / CATALOG_PATH)
    multi_crops = set()
    for entry in combos.values():
        if entry is None:
            continue
        multi_crops.update(entry["crops"])
    overlap_multi = sorted(set(cropgrids_crops) & multi_crops)
    if overlap_multi:
        raise ValueError(
            "cropgrids_crops cannot participate in multiple_cropping "
            f"combinations: {', '.join(overlap_multi)}"
        )

    mapping_path = project_root / "data" / "curated" / "cropgrids_crop_mapping.csv"
    if not mapping_path.exists():
        raise FileNotFoundError(f"Expected data file at {mapping_path}")
    mapping = pd.read_csv(mapping_path, comment="#")
    required_cols = {"crop", "cropgrids_name", "faostat_qcl_item_code"}
    missing_cols = required_cols - set(mapping.columns)
    if missing_cols:
        raise ValueError(
            f"cropgrids_crop_mapping.csv missing columns: {sorted(missing_cols)}"
        )
    mapping["crop"] = mapping["crop"].astype(str).str.strip()
    mapped_crops = set(mapping["crop"])
    missing_mapping = sorted(set(cropgrids_crops) - mapped_crops)
    if missing_mapping:
        raise ValueError(
            "cropgrids_crops missing from cropgrids_crop_mapping.csv: "
            f"{', '.join(missing_mapping)}"
        )
    for _, row in mapping.iterrows():
        if row["crop"] not in cropgrids_crops:
            continue
        if not str(row["cropgrids_name"]).strip():
            raise ValueError(
                f"cropgrids_crop_mapping.csv: empty cropgrids_name for {row['crop']}"
            )
        if (
            pd.isna(row["faostat_qcl_item_code"])
            or not str(row["faostat_qcl_item_code"]).strip()
        ):
            raise ValueError(
                "cropgrids_crop_mapping.csv: empty faostat_qcl_item_code for "
                f"{row['crop']}"
            )

    gaez_mapping_path = project_root / "data" / "curated" / "gaez_crop_code_mapping.csv"
    if gaez_mapping_path.exists():
        gaez_mapping = pd.read_csv(gaez_mapping_path)
        gaez_mapped = set(gaez_mapping["crop_name"].astype(str).str.strip())
        overlap_gaez = sorted(set(cropgrids_crops) & gaez_mapped)
        if overlap_gaez:
            raise ValueError(
                "cropgrids_crops must not appear in gaez_crop_code_mapping.csv: "
                f"{', '.join(overlap_gaez)}"
            )
