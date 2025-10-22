# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Parse GLEAM supplement nutritional data and create feed properties table.

Combines GLEAM feed nutritional values with model entity mapping to produce
a unified feed properties database with energy and nitrogen content.
"""

import logging

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_gleam_ruminant_nutrition(excel_file: str) -> pd.DataFrame:
    """Parse Table S.3.3 - Ruminant feed nutritional values.

    Note: The column header says "kJ/kgDM" but values are actually in MJ/kg DM
    based on typical feed energy ranges (15-20 MJ/kg is normal).
    """
    # Read the table, skipping header rows
    df = pd.read_excel(excel_file, sheet_name="Tab. S.3.3", header=None, skiprows=2)

    # Drop the first (empty) column
    df = df.iloc[:, 1:]

    # Set column names (note: GE values are actually in MJ despite header)
    df.columns = ["Number", "Material", "GE_MJ_per_kg_DM", "N_g_per_kg_DM", "DI_pct"]

    # Remove rows without material codes (section headers, footnotes)
    df = df[df["Material"].notna()].copy()
    df = df[df["Material"].str.len() > 0]

    # Filter to only rows with GLEAM codes (uppercase, specific patterns)
    df = df[df["Material"].str.isupper()].copy()

    # Clean footnote markers (asterisks) from numeric columns
    for col in ["GE_MJ_per_kg_DM", "N_g_per_kg_DM", "DI_pct"]:
        df[col] = df[col].astype(str).str.replace("*", "", regex=False)

    # Convert numeric columns
    df["GE_MJ_per_kg_DM"] = pd.to_numeric(df["GE_MJ_per_kg_DM"], errors="coerce")
    df["N_g_per_kg_DM"] = pd.to_numeric(df["N_g_per_kg_DM"], errors="coerce")
    df["DI_pct"] = pd.to_numeric(df["DI_pct"], errors="coerce")

    # Rename for consistency
    df = df.rename(columns={"Material": "gleam_code"})
    df = df[["gleam_code", "GE_MJ_per_kg_DM", "N_g_per_kg_DM", "DI_pct"]]

    logger.info("Parsed %d ruminant feed entries from GLEAM Table S.3.3", len(df))

    return df


def parse_gleam_monogastric_nutrition(excel_file: str) -> pd.DataFrame:
    """Parse Table S.3.4 - Monogastric feed nutritional values.

    Note: GE column header says "kJ" but values are actually in MJ/kg DM.
    ME columns correctly state kJ and need conversion to MJ.
    """
    # Read the table, skipping header rows
    df = pd.read_excel(excel_file, sheet_name="Tab. S.3.4", header=None, skiprows=2)

    # Drop the first (empty) column
    df = df.iloc[:, 1:]

    # Set column names based on structure
    df.columns = [
        "Number",
        "Material",
        "GE_kJ_per_kg_DM",  # In kJ, needs conversion to MJ
        "N_g_per_kg_DM",
        "ME_chickens_kJ_per_kg_DM",  # In kJ, needs conversion
        "ME_pigs_kJ_per_kg_DM",  # In kJ, needs conversion
        "DI_pct",
    ]

    # Remove rows without material codes
    df = df[df["Material"].notna()].copy()
    df = df[df["Material"].str.len() > 0]

    # Filter to only rows with GLEAM codes
    df = df[df["Material"].str.isupper()].copy()

    # Clean footnote markers (asterisks) from numeric columns
    numeric_cols = [
        "GE_kJ_per_kg_DM",
        "N_g_per_kg_DM",
        "ME_chickens_kJ_per_kg_DM",
        "ME_pigs_kJ_per_kg_DM",
        "DI_pct",
    ]
    for col in numeric_cols:
        df[col] = df[col].astype(str).str.replace("*", "", regex=False)

    # Convert numeric columns
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Convert GE and ME from kJ to MJ
    df["GE_MJ_per_kg_DM"] = df["GE_kJ_per_kg_DM"] / 1000.0
    df["ME_pigs_MJ_per_kg_DM"] = df["ME_pigs_kJ_per_kg_DM"] / 1000.0

    # Rename for consistency
    df = df.rename(columns={"Material": "gleam_code"})
    df = df[
        [
            "gleam_code",
            "GE_MJ_per_kg_DM",
            "N_g_per_kg_DM",
            "ME_pigs_MJ_per_kg_DM",
            "DI_pct",
        ]
    ]

    logger.info("Parsed %d monogastric feed entries from GLEAM Table S.3.4", len(df))

    return df


def create_feed_properties(
    gleam_supplement: str,
    gleam_mapping: str,
    output_ruminant: str,
    output_monogastric: str,
) -> None:
    """
    Create separate feed properties tables for ruminants and monogastrics.

    Parameters
    ----------
    gleam_supplement : str
        Path to GLEAM supplement Excel file
    gleam_mapping : str
        Path to GLEAM feed mapping CSV
    output_ruminant : str
        Path to output ruminant feed properties CSV
    output_monogastric : str
        Path to output monogastric feed properties CSV
    """
    # Parse GLEAM nutritional tables
    ruminant_nutrition = parse_gleam_ruminant_nutrition(gleam_supplement)
    monogastric_nutrition = parse_gleam_monogastric_nutrition(gleam_supplement)

    # Read GLEAM mapping and filter out skipped feeds
    mapping = pd.read_csv(gleam_mapping, comment="#")
    mapping = mapping[mapping["model_entity"].notna()].copy()
    mapping = mapping[~mapping["notes"].str.contains("not in model", na=False)]
    mapping = mapping[~mapping["notes"].str.contains("skip for now", na=False)]
    mapping = mapping[~mapping["notes"].str.contains("not modeled", na=False)]

    logger.info("Loaded %d feed mappings (after filtering)", len(mapping))

    # Convert digestibility from percentage to fraction
    ruminant_nutrition["digestibility"] = ruminant_nutrition["DI_pct"] / 100.0
    monogastric_nutrition["digestibility"] = monogastric_nutrition["DI_pct"] / 100.0

    # === RUMINANT FEEDS ===
    ruminant_feeds = mapping[mapping["animal_type"].isin(["ruminant", "both"])].copy()
    ruminant_merged = ruminant_feeds.merge(
        ruminant_nutrition[
            ["gleam_code", "GE_MJ_per_kg_DM", "N_g_per_kg_DM", "digestibility"]
        ],
        on="gleam_code",
        how="left",
    )

    # Keep only rows with nutritional data
    ruminant_merged = ruminant_merged.dropna(
        subset=["GE_MJ_per_kg_DM", "digestibility"]
    )

    # Select and rename columns
    ruminant_output = ruminant_merged[
        [
            "model_entity",
            "entity_type",
            "GE_MJ_per_kg_DM",
            "N_g_per_kg_DM",
            "digestibility",
            "gleam_code",
        ]
    ].rename(columns={"model_entity": "feed_item", "entity_type": "source_type"})

    # Sort and write
    ruminant_output = ruminant_output.sort_values(["source_type", "feed_item"])

    ruminant_header = """# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: CC0-1.0
#
# Ruminant feed properties from GLEAM 3.0 Supplement S1 Table S.3.3
#
# Source: FAO (2022). Global Livestock Environmental Assessment Model (GLEAM).
#         Supplement S1, Table S.3.3 (Ruminant nutrition).
#         https://www.fao.org/gleam/
#
# Units:
#   GE_MJ_per_kg_DM: Gross energy in MJ per kg dry matter
#   N_g_per_kg_DM: Nitrogen content in g per kg dry matter
#   digestibility: Digestibility for ruminants (fraction 0-1)
#
"""

    with open(output_ruminant, "w") as f:
        f.write(ruminant_header)
        ruminant_output.to_csv(f, index=False)

    logger.info(
        "Created ruminant feed properties with %d feeds, written to %s",
        len(ruminant_output),
        output_ruminant,
    )

    # === MONOGASTRIC FEEDS ===
    monogastric_feeds = mapping[
        mapping["animal_type"].isin(["monogastric", "both"])
    ].copy()
    monogastric_merged = monogastric_feeds.merge(
        monogastric_nutrition[
            [
                "gleam_code",
                "GE_MJ_per_kg_DM",
                "N_g_per_kg_DM",
                "ME_pigs_MJ_per_kg_DM",
                "digestibility",
            ]
        ],
        on="gleam_code",
        how="left",
    )

    # Keep only rows with nutritional data
    monogastric_merged = monogastric_merged.dropna(
        subset=["GE_MJ_per_kg_DM", "digestibility"]
    )

    # Select and rename columns
    monogastric_output = monogastric_merged[
        [
            "model_entity",
            "entity_type",
            "GE_MJ_per_kg_DM",
            "ME_pigs_MJ_per_kg_DM",
            "N_g_per_kg_DM",
            "digestibility",
            "gleam_code",
        ]
    ].rename(
        columns={
            "model_entity": "feed_item",
            "entity_type": "source_type",
            "ME_pigs_MJ_per_kg_DM": "ME_MJ_per_kg_DM",
        }
    )

    # Sort and write
    monogastric_output = monogastric_output.sort_values(["source_type", "feed_item"])

    monogastric_header = """# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: CC0-1.0
#
# Monogastric feed properties from GLEAM 3.0 Supplement S1 Table S.3.4
#
# Source: FAO (2022). Global Livestock Environmental Assessment Model (GLEAM).
#         Supplement S1, Table S.3.4 (Monogastric nutrition - pigs).
#         https://www.fao.org/gleam/
#
# Units:
#   GE_MJ_per_kg_DM: Gross energy in MJ per kg dry matter
#   ME_MJ_per_kg_DM: Metabolizable energy for pigs in MJ per kg DM
#   N_g_per_kg_DM: Nitrogen content in g per kg dry matter
#   digestibility: Digestibility for monogastrics (fraction 0-1)
#
"""

    with open(output_monogastric, "w") as f:
        f.write(monogastric_header)
        monogastric_output.to_csv(f, index=False)

    logger.info(
        "Created monogastric feed properties with %d feeds, written to %s",
        len(monogastric_output),
        output_monogastric,
    )

    # Log summary statistics
    logger.info("Ruminant feeds by source:")
    logger.info("  Crops: %d", (ruminant_output["source_type"] == "crop").sum())
    logger.info("  Foods: %d", (ruminant_output["source_type"] == "food").sum())
    logger.info("  Residues: %d", (ruminant_output["source_type"] == "residue").sum())
    logger.info("  Forage: %d", (ruminant_output["source_type"] == "forage").sum())

    logger.info("Monogastric feeds by source:")
    logger.info("  Crops: %d", (monogastric_output["source_type"] == "crop").sum())
    logger.info("  Foods: %d", (monogastric_output["source_type"] == "food").sum())
    logger.info(
        "  Residues: %d", (monogastric_output["source_type"] == "residue").sum()
    )


if __name__ == "__main__":
    create_feed_properties(
        gleam_supplement=snakemake.input.gleam_supplement,
        gleam_mapping=snakemake.input.gleam_mapping,
        output_ruminant=snakemake.output.ruminant,
        output_monogastric=snakemake.output.monogastric,
    )
