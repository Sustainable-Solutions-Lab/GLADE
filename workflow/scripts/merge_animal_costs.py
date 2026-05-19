# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Merge animal product production costs from multiple sources (USDA, FADN, etc.) into
a single unified cost dataset.

For products without direct source data, two fallback layers are applied
in order:

1. ``fallback_aliases``: copy another product's per-tonne cost verbatim
   (e.g. ``dairy-buffalo -> dairy``).
2. ``fallback_values_usd_per_t``: literature-based per-product defaults
   with separate non-grazing ``production`` and ``grazing`` components,
   mirroring the source-data column convention (USDA's grazing items go
   into the grazing column; everything else into the production column).

Products that remain unresolved after both layers receive zero costs
with a logged warning. See ``config/default.yaml`` (``animal_costs``
section) and ``docs/costs.rst`` for the source citations.

Inputs
- snakemake.input.cost_sources: List of cost CSV files from different sources (USDA, FADN, etc.)
- snakemake.params.animal_products: List of all model animal products from config
- snakemake.params.base_year: Base year for cost values (for column naming)
- snakemake.params.fallback_aliases: Dict mapping product -> proxy product
- snakemake.params.fallback_values_usd_per_t: Dict mapping product ->
    {"production": float, "grazing": float}

Output
- snakemake.output.costs: CSV with columns:
    product, n_sources, source, cost_per_t_usd_{base_year},
    grazing_cost_per_t_usd_{base_year}

  ``source`` annotates how each row was resolved: ``"data"`` (averaged
  source data), ``"alias:<proxy>"``, ``"literature"``, or ``"zero"``.
"""

import logging
from pathlib import Path

import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def load_cost_sources(source_paths: list[str], base_year: int) -> pd.DataFrame:
    """
    Load and concatenate cost data from multiple sources.

    Returns DataFrame with columns: product, source, cost_per_t_usd_{base_year}, grazing_cost_per_t_usd_{base_year}
    """
    cost_column = f"cost_per_t_usd_{base_year}"
    grazing_column = f"grazing_cost_per_t_usd_{base_year}"

    all_costs = []

    for source_path in source_paths:
        source_name = Path(
            source_path
        ).stem  # e.g., "usda_animal_costs" or "fadn_animal_costs"
        logger.info(f"Loading cost data from {source_name}")

        df = pd.read_csv(source_path)

        # Check for required columns
        if "product" not in df.columns:
            logger.warning(f"No 'product' column in {source_name}, skipping")
            continue

        if cost_column not in df.columns:
            logger.warning(f"Missing cost column in {source_name}, skipping")
            continue

        # Check for grazing cost column (optional in source, default to 0)
        if grazing_column not in df.columns:
            df[grazing_column] = 0.0

        # Extract relevant columns
        df_subset = df[["product", cost_column, grazing_column]].copy()
        df_subset["source"] = source_name
        df_subset = df_subset.dropna(subset=[cost_column])

        logger.info(f"  Loaded {len(df_subset)} cost entries from {source_name}")
        all_costs.append(df_subset)

    if not all_costs:
        logger.warning("No cost data loaded from any source")
        return pd.DataFrame()

    combined = pd.concat(all_costs, ignore_index=True)
    logger.info(f"Total cost entries across all sources: {len(combined)}")

    return combined


def merge_costs(
    costs_df: pd.DataFrame,
    all_products: list[str],
    base_year: int,
    fallback_aliases: dict[str, str],
    fallback_values_usd_per_t: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """
    Merge costs from sources and resolve fallbacks for products without data.

    Resolution order per product: source data -> alias -> literature -> zero.
    The ``source`` column annotates which branch was taken.
    """
    cost_column = f"cost_per_t_usd_{base_year}"
    grazing_column = f"grazing_cost_per_t_usd_{base_year}"

    # Step 1: Average costs for products with multiple sources
    averaged_costs = (
        costs_df.groupby("product")
        .agg(
            {
                cost_column: "mean",
                grazing_column: "mean",
                "source": "count",  # Count number of sources
            }
        )
        .rename(columns={"source": "n_sources"})
        .reset_index()
    )

    logger.info(f"Averaged costs for {len(averaged_costs)} products with direct data")

    for _, row in averaged_costs.iterrows():
        if row["n_sources"] > 1:
            logger.info(
                f"  {row['product']}: averaged from {row['n_sources']} sources "
                f"(Prod: ${row[cost_column]:.2f}/t, Grazing: ${row[grazing_column]:.2f}/t)"
            )

    # Step 2: Create cost dictionary for easy lookup
    cost_dict = averaged_costs.set_index("product")[
        [cost_column, grazing_column, "n_sources"]
    ].to_dict("index")

    # Step 3: Resolve per product, following the fallback chain
    results = []

    for product in all_products:
        if product in cost_dict:
            results.append(
                {
                    "product": product,
                    "n_sources": int(cost_dict[product]["n_sources"]),
                    "source": "data",
                    cost_column: cost_dict[product][cost_column],
                    grazing_column: cost_dict[product][grazing_column],
                }
            )
            continue

        if product in fallback_aliases:
            proxy = fallback_aliases[product]
            if proxy not in cost_dict:
                raise KeyError(
                    f"Alias for '{product}' points to '{proxy}', which has no "
                    "source data. Provide source data for the proxy, or remove "
                    "the alias."
                )
            logger.info(
                "  %s: aliased to %s (Prod: $%.2f/t, Grazing: $%.2f/t)",
                product,
                proxy,
                cost_dict[proxy][cost_column],
                cost_dict[proxy][grazing_column],
            )
            results.append(
                {
                    "product": product,
                    "n_sources": 0,
                    "source": f"alias:{proxy}",
                    cost_column: cost_dict[proxy][cost_column],
                    grazing_column: cost_dict[proxy][grazing_column],
                }
            )
            continue

        if product in fallback_values_usd_per_t:
            entry = fallback_values_usd_per_t[product]
            try:
                prod_cost = float(entry["production"])
                grazing_cost = float(entry["grazing"])
            except KeyError as exc:
                raise KeyError(
                    f"fallback_values_usd_per_t entry for '{product}' must "
                    "have 'production' and 'grazing' keys"
                ) from exc
            logger.info(
                "  %s: literature fallback (Prod: $%.2f/t, Grazing: $%.2f/t)",
                product,
                prod_cost,
                grazing_cost,
            )
            results.append(
                {
                    "product": product,
                    "n_sources": 0,
                    "source": "literature",
                    cost_column: prod_cost,
                    grazing_column: grazing_cost,
                }
            )
            continue

        logger.warning(f"No cost data for {product}, using zero costs")
        results.append(
            {
                "product": product,
                "n_sources": 0,
                "source": "zero",
                cost_column: 0.0,
                grazing_column: 0.0,
            }
        )

    columns = ["product", "n_sources", "source", cost_column, grazing_column]
    return pd.DataFrame(results, columns=columns)


def main():
    cost_source_paths: list[str] = list(snakemake.input.cost_sources)  # type: ignore[name-defined]
    all_products: list[str] = list(snakemake.params.animal_products)  # type: ignore[name-defined]
    base_year: int = int(snakemake.params.base_year)  # type: ignore[name-defined]
    fallback_aliases: dict[str, str] = dict(snakemake.params.fallback_aliases)  # type: ignore[name-defined]
    fallback_values_usd_per_t: dict[str, dict[str, float]] = dict(
        snakemake.params.fallback_values_usd_per_t  # type: ignore[name-defined]
    )
    out_path: str = snakemake.output.costs  # type: ignore[name-defined]

    logger.info(
        f"Merging animal costs from {len(cost_source_paths)} sources for {len(all_products)} products"
    )

    # Load all cost sources
    costs_df = load_cost_sources(cost_source_paths, base_year)

    # Merge and apply fallbacks
    merged_costs = merge_costs(
        costs_df,
        all_products,
        base_year,
        fallback_aliases,
        fallback_values_usd_per_t,
    )

    # Write output
    merged_costs.to_csv(out_path, index=False)
    logger.info(
        f"Wrote merged cost data for {len(merged_costs)} products to {out_path}"
    )

    # Summary statistics
    source_counts = merged_costs["source"].value_counts().to_dict()
    with_zero = (merged_costs[f"cost_per_t_usd_{base_year}"] == 0).sum()
    with_zero_grazing = (merged_costs[f"grazing_cost_per_t_usd_{base_year}"] == 0).sum()

    logger.info(
        "Summary: resolution = %s; %d products with zero production costs, "
        "%d with zero grazing costs",
        source_counts,
        int(with_zero),
        int(with_zero_grazing),
    )


if __name__ == "__main__":
    # Configure logging
    setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)  # type: ignore[name-defined]

    main()
