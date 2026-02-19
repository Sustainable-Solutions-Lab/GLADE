# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared constants and helpers for animal-product scripts."""

import logging

import pandas as pd

from workflow.scripts.faostat_bulk import (
    add_iso3_column,
    get_item_map,
    load_bulk_csv,
    load_m49_to_iso3,
)

logger = logging.getLogger(__name__)

# Species -> products (for grouping product-level data back to species)
SPECIES_PRODUCTS = {
    "Cattle & buffaloes": ["dairy", "dairy-buffalo", "meat-cattle"],
    "Small Ruminants": ["meat-sheep"],
    "Poultry": ["eggs", "meat-chicken"],
    "Pigs": ["meat-pig"],
}


def load_faostat_qcl(
    qcl_csv_path: str,
    m49_codes_path: str | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Load a FAOSTAT QCL bulk CSV and return the DataFrame with item map.

    If *m49_codes_path* is provided, an ``iso3`` column is added to the
    returned DataFrame via the M49-to-ISO3 mapping.

    Returns ``(bulk_df, item_map)`` where *item_map* maps FAOSTAT item
    labels to item codes.
    """
    logger.info("Loading FAOSTAT QCL bulk CSV")
    bulk = load_bulk_csv(qcl_csv_path)
    item_map = get_item_map(bulk)
    if m49_codes_path is not None:
        m49_to_iso3 = load_m49_to_iso3(m49_codes_path)
        bulk = add_iso3_column(bulk, m49_to_iso3)
    return bulk, item_map
