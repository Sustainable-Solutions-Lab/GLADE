# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rules for optimal taxes/subsidies workflow.

This workflow:
1. Runs optimization with health/GHG objectives to derive optimal consumption
2. Extracts consumption patterns from the optimized model
3. Fixes consumption to optimal values and solves with production costs only
4. Extracts dual variables as optimal Pigouvian taxes/subsidies
5. Resolves the model with taxes/subsidies applied in the objective
"""

plotting_cfg = config.get("plotting", {})


rule extract_optimal_consumption:
    """Extract consumption by food group from optimized model (Stage 1 output).

    Consumption is extracted from food group stores in the solved network,
    converted to per-capita g/day format for use as constraints in Stage 2.
    Population data is read from the network metadata.
    """
    input:
        network="results/{name}/solved/model_scen-optimize.nc",
        food_groups="data/curated/food_groups.csv",
    output:
        consumption="results/{name}/optimal_taxes/optimal_consumption.csv",
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "logs/{name}/extract_optimal_consumption.log",
    benchmark:
        "benchmarks/{name}/extract_optimal_consumption.tsv"
    script:
        "../scripts/extract_optimal_consumption.py"


rule extract_optimal_taxes:
    """Extract optimal taxes/subsidies from consumption constraint duals.

    Taxes are the dual variables (shadow prices) of the food group
    equality constraints from Stage 2, representing the Pigouvian
    tax/subsidy needed to incentivize optimal consumption.
    """
    input:
        network="results/{name}/solved/model_scen-extract_taxes.nc",
    output:
        taxes="results/{name}/optimal_taxes/taxes.csv",
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "logs/{name}/extract_optimal_taxes.log",
    benchmark:
        "benchmarks/{name}/extract_optimal_taxes.tsv"
    script:
        "../scripts/extract_optimal_taxes.py"


rule plot_optimal_taxes:
    """Visualize optimal taxes/subsidies by food group."""
    input:
        taxes="results/{name}/optimal_taxes/taxes.csv",
    output:
        taxes_pdf="results/{name}/plots/optimal_taxes/taxes_by_food_group.pdf",
        taxes_csv="results/{name}/plots/optimal_taxes/taxes_by_food_group.csv",
    params:
        group_colors=plotting_cfg.get("colors", {}).get("food_groups", {}),
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "logs/{name}/plot_optimal_taxes.log",
    benchmark:
        "benchmarks/{name}/plot_optimal_taxes.tsv"
    script:
        "../scripts/plotting/plot_optimal_taxes.py"


rule plot_optimal_taxes_diet_comparison:
    """Compare global average diet across the optimal taxes optimizations."""
    input:
        networks=[
            "results/{name}/solved/model_scen-optimize.nc",
            "results/{name}/solved/model_scen-extract_taxes.nc",
            "results/{name}/solved/model_scen-apply_taxes.nc",
        ],
        food_groups="data/curated/food_groups.csv",
    output:
        pdf="results/{name}/plots/optimal_taxes/diet_comparison.pdf",
        csv="results/{name}/plots/optimal_taxes/diet_comparison.csv",
    params:
        wildcards=[
            "Health/GHG optimized",
            "Fixed consumption (costs only)",
            "Taxes in objective",
        ],
        group_colors=plotting_cfg.get("colors", {}).get("food_groups", {}),
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "logs/{name}/plot_optimal_taxes_diet_comparison.log",
    benchmark:
        "benchmarks/{name}/plot_optimal_taxes_diet_comparison.tsv"
    script:
        "../scripts/plotting/plot_food_consumption_comparison.py"
