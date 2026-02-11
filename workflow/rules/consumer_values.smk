# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rules for consumer values workflow.

This workflow:
1. Extracts "consumer values" (dual variables) from a baseline model with fixed
   consumption (enforce_baseline_diet=True)
2. Calibrates piecewise utility blocks from extracted values and baseline food
   consumption levels
3. Uses these values in subsequent solves to explore how health/environmental
   pricing affects consumption while accounting for revealed consumer preferences
"""


rule extract_consumer_values:
    """Extract consumer values from baseline solve with fixed consumption.

    Consumer values are the dual variables (shadow prices) of the food group
    equality constraints, representing the marginal value of consumption.
    """
    input:
        network="<results>/{name}/solved/model_scen-baseline.nc",
    output:
        consumer_values="<results>/{name}/consumer_values/values.csv",
    group:
        "prep"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/extract_consumer_values.log",
    benchmark:
        "<benchmarks>/{name}/extract_consumer_values.tsv"
    script:
        "../scripts/extract_consumer_values.py"


rule calibrate_food_utility_blocks:
    """Calibrate piecewise food utility blocks from baseline dual values."""
    input:
        network="<results>/{name}/solved/model_scen-baseline.nc",
        consumer_values="<results>/{name}/consumer_values/values.csv",
    output:
        utility_blocks="<results>/{name}/consumer_values/utility_blocks.csv",
    params:
        n_blocks=config["food_utility_piecewise"]["n_blocks"],
        decline_factor=config["food_utility_piecewise"]["decline_factor"],
        total_width_multiplier=config["food_utility_piecewise"][
            "total_width_multiplier"
        ],
    group:
        "prep"
    log:
        "<logs>/{name}/calibrate_food_utility_blocks.log",
    script:
        "../scripts/calibrate_food_utility_blocks.py"


# Consumer values comparison scenarios (from scenario definitions)
CV_SCENARIOS = list_scenarios()
if not CV_SCENARIOS:
    raise ValueError("Missing scenario_defs in config for consumer values workflow")


def consumer_values_comparison_inputs(wildcards):
    """Get analysis outputs for consumer values comparison."""
    inputs = {}
    for scen in CV_SCENARIOS:
        inputs[f"consumption_{scen}"] = (
            f"<results>/{wildcards.name}/analysis/scen-{scen}/food_group_consumption.csv"
        )
        inputs[f"breakdown_{scen}"] = (
            f"<results>/{wildcards.name}/analysis/scen-{scen}/objective_breakdown.csv"
        )
    return inputs


rule plot_consumer_values_comparison:
    """Compare consumption and objective breakdown across consumer values scenarios."""
    input:
        unpack(consumer_values_comparison_inputs),
        consumer_values="<results>/{name}/consumer_values/values.csv",
    output:
        consumption_pdf="<results>/{name}/plots/consumer_values/consumption_comparison.pdf",
        consumption_csv="<results>/{name}/plots/consumer_values/consumption_comparison.csv",
        objective_pdf="<results>/{name}/plots/consumer_values/objective_comparison.pdf",
        objective_csv="<results>/{name}/plots/consumer_values/objective_comparison.csv",
        cv_pdf="<results>/{name}/plots/consumer_values/consumer_values.pdf",
        cv_csv="<results>/{name}/plots/consumer_values/consumer_values.csv",
    params:
        scenarios=CV_SCENARIOS,
        group_colors=plotting_cfg.get("colors", {}).get("food_groups", {}),
    group:
        "analysis_plot"
    resources:
        runtime="5m",
        mem_mb=2000,
    log:
        "<logs>/{name}/plot_consumer_values_comparison.log",
    benchmark:
        "<benchmarks>/{name}/plot_consumer_values_comparison.tsv"
    script:
        "../scripts/plotting/plot_consumer_values_comparison.py"
