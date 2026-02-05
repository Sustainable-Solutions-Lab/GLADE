# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rules for consumer values workflow.

This workflow:
1. Extracts "consumer values" (dual variables) from a baseline model with fixed
   consumption (enforce_baseline_diet=True)
2. Uses these values in subsequent solves to explore how health/environmental
   pricing affects consumption while accounting for revealed consumer preferences
"""


rule extract_consumer_values:
    """Extract consumer values from baseline solve with fixed consumption.

    Consumer values are the dual variables (shadow prices) of the food group
    equality constraints, representing the marginal value of consumption.
    """
    input:
        network="results/{name}/solved/model_scen-baseline.nc",
    output:
        consumer_values="results/{name}/consumer_values/values.csv",
    log:
        "logs/{name}/extract_consumer_values.log",
    script:
        "../scripts/extract_consumer_values.py"


# Consumer values comparison scenarios (from scenario definitions)
CV_SCENARIOS = list_scenarios()
if not CV_SCENARIOS:
    raise ValueError("Missing scenario_defs in config for consumer values workflow")


def consumer_values_comparison_inputs(wildcards):
    """Get analysis outputs for consumer values comparison."""
    inputs = {}
    for scen in CV_SCENARIOS:
        inputs[f"consumption_{scen}"] = (
            f"results/{wildcards.name}/analysis/scen-{scen}/food_group_consumption.csv"
        )
        inputs[f"breakdown_{scen}"] = (
            f"results/{wildcards.name}/analysis/scen-{scen}/objective_breakdown.csv"
        )
    return inputs


rule plot_consumer_values_comparison:
    """Compare consumption and objective breakdown across consumer values scenarios."""
    input:
        unpack(consumer_values_comparison_inputs),
        consumer_values="results/{name}/consumer_values/values.csv",
    output:
        consumption_pdf="results/{name}/plots/consumer_values/consumption_comparison.pdf",
        consumption_csv="results/{name}/plots/consumer_values/consumption_comparison.csv",
        objective_pdf="results/{name}/plots/consumer_values/objective_comparison.pdf",
        objective_csv="results/{name}/plots/consumer_values/objective_comparison.csv",
        cv_pdf="results/{name}/plots/consumer_values/consumer_values.pdf",
        cv_csv="results/{name}/plots/consumer_values/consumer_values.csv",
    params:
        scenarios=CV_SCENARIOS,
        group_colors=plotting_cfg.get("colors", {}).get("food_groups", {}),
    log:
        "logs/{name}/plot_consumer_values_comparison.log",
    script:
        "../scripts/plotting/plot_consumer_values_comparison.py"
