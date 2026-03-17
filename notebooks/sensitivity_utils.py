# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared utilities for sensitivity analysis notebooks.

This module contains common functions used by yll_sensitivity, ghg_sensitivity,
and combined_sensitivity notebooks.
"""

from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt
from matplotlib.transforms import blended_transform_factory
import numpy as np
import pandas as pd
import yaml

# Import constants from workflow instead of redefining
from workflow.scripts.constants import (
    DAYS_PER_YEAR,
    PJ_TO_KCAL,
)

# GWP values (AR5 100-year)
CH4_GWP = 28.0
N2O_GWP = 265.0

# Figure styling constants
FIGURE_WIDTH_MM = 180  # Standard figure width in mm
MM_TO_INCH = 1 / 25.4  # Conversion factor

# Font sizes (in points)
FONTSIZE_TITLE = 7
FONTSIZE_AXIS_LABEL = 6
FONTSIZE_TICK_LABEL = 5
FONTSIZE_CBAR_LABEL = 6
FONTSIZE_PANEL_LABEL = 8
FONTSIZE_CONTOUR_LABEL = 5


def log_scale_zero_position(x_values: np.ndarray) -> float:
    """Calculate the position at which to plot x=0 on a log scale for even spacing.

    Given a sequence of x values that includes 0 and follows a geometric
    progression (e.g., 0, 1, 2, 4, 8, ...), this function calculates where
    to plot the 0 value to maintain even spacing on a log scale.

    The formula is: zero_pos = x1² / x2, where x1 and x2 are the first two
    non-zero values.

    Args:
        x_values: Array of x values, may include 0.

    Returns:
        The x position at which to plot 0 for even log-scale spacing.
        Returns 0.5 as fallback if calculation is not possible.
    """
    non_zero = np.sort(x_values[x_values > 0])
    if len(non_zero) >= 2:
        x1, x2 = non_zero[0], non_zero[1]
        return x1 * x1 / x2
    return 0.5  # Fallback


# Pretty names for food groups (including aggregated groups)
PRETTY_NAMES = {
    "grain": "Refined grains",
    "whole_grains": "Whole grains",
    "dairy": "Dairy",
    "eggs": "Eggs",
    "fruits": "Fruits",
    "legumes": "Legumes",
    "nuts_seeds": "Nuts & seeds",
    "oil": "Oil",
    "poultry": "Poultry",
    "red_meat": "Red meat",
    "starchy_vegetable": "Starchy veg.",
    "stimulants": "Stimulants",
    "sugar": "Sugar",
    "vegetables": "Vegetables",
    "fruits_vegetables": "Fruits & veg.",
    "eggs_poultry": "Eggs & poultry",
}

# Health-specific labels: "Diet low in X" for protective, "Diet high in X" for harmful
PRETTY_NAMES_HEALTH = {
    "fruits": "Diet low in\nfruits",
    "vegetables": "Diet low in\nvegetables",
    "whole_grains": "Diet low in\nwhole grains",
    "legumes": "Diet low in\nlegumes",
    "nuts_seeds": "Diet low in\nnuts & seeds",
    "red_meat": "Diet high in\nred meat",
    "fruits_vegetables": "Diet low in\nfruits & veg.",
}

# Pretty names for objective categories
PRETTY_NAMES_OBJ = {
    "Crop production": "Crop production",
    "Trade": "Trade",
    "Health burden": "Health burden",
    "GHG cost": "GHG cost",
    "GHG cost (positive)": "GHG cost",
    "GHG cost (negative)": "GHG cost",
    "Fertilizer (synthetic)": "Fertilizer",
    "Consumer values": "Consumer values",
    "Biomass exports": "Biomass exports",
}


# -----------------------------------------------------------------------------
# Config and scenario loading utilities
# -----------------------------------------------------------------------------


def load_scenario_defs(project_root: Path, config_name: str) -> dict:
    """Load and expand scenario definitions from a config file.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')

    Returns:
        Dict of expanded scenario definitions
    """
    # Add workflow directory to path for importing scenario_generators
    workflow_path = project_root / "workflow"
    if str(workflow_path) not in sys.path:
        sys.path.insert(0, str(workflow_path))

    from scenario_generators import expand_scenario_defs

    config_path = project_root / "config" / f"{config_name}.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    raw_defs = config.get("scenarios") or {}
    return expand_scenario_defs(raw_defs)


def extract_scenarios_with_param(
    project_root: Path,
    config_name: str,
    param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, str, Path]]:
    """Extract scenarios and their parameter values from config.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll')
        param_path: Path to parameter in scenario config (e.g., ['emissions', 'ghg_price'])
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg_', 'yll_')

    Returns:
        List of (param_value, scenario_name, network_path) tuples, sorted by param_value
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to parameter value
        value = scenario_config
        for key in param_path:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                value = None
                break

        if value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append((float(value), scenario_name, network_path))

    scenarios.sort(key=lambda x: x[0])
    return scenarios


def extract_combined_scenarios(
    project_root: Path,
    config_name: str,
    ghg_param_path: list[str],
    yll_param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, float, str, Path]]:
    """Extract scenarios with both GHG price and YLL value parameters.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll')
        ghg_param_path: Path to GHG price in scenario config
        yll_param_path: Path to YLL value in scenario config
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg_yll_')

    Returns:
        List of (ghg_price, yll_value, scenario_name, network_path) tuples,
        sorted by ghg_price
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to GHG price
        ghg_value = scenario_config
        for key in ghg_param_path:
            if isinstance(ghg_value, dict) and key in ghg_value:
                ghg_value = ghg_value[key]
            else:
                ghg_value = None
                break

        # Navigate to YLL value
        yll_value = scenario_config
        for key in yll_param_path:
            if isinstance(yll_value, dict) and key in yll_value:
                yll_value = yll_value[key]
            else:
                yll_value = None
                break

        if ghg_value is not None and yll_value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append(
                (float(ghg_value), float(yll_value), scenario_name, network_path)
            )

    scenarios.sort(key=lambda x: x[0])
    return scenarios


def extract_grid_scenarios(
    project_root: Path,
    config_name: str,
    ghg_param_path: list[str],
    yll_param_path: list[str],
    scenario_prefix: str,
) -> list[tuple[float, float, str, Path]]:
    """Extract grid scenarios with both GHG price and YLL value parameters.

    Unlike extract_combined_scenarios which expects co-varying parameters,
    this function extracts all combinations from a 2D grid.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll_grid')
        ghg_param_path: Path to GHG price in scenario config
        yll_param_path: Path to YLL value in scenario config
        scenario_prefix: Prefix to match scenario names (e.g., 'ghg')

    Returns:
        List of (ghg_price, yll_value, scenario_name, network_path) tuples,
        sorted by (ghg_price, yll_value)
    """
    scenario_defs = load_scenario_defs(project_root, config_name)
    results_dir = project_root / "results" / config_name / "solved"

    scenarios = []
    for scenario_name, scenario_config in scenario_defs.items():
        if scenario_name == "baseline" or not scenario_name.startswith(scenario_prefix):
            continue

        # Navigate to GHG price
        ghg_value = scenario_config
        for key in ghg_param_path:
            if isinstance(ghg_value, dict) and key in ghg_value:
                ghg_value = ghg_value[key]
            else:
                ghg_value = None
                break

        # Navigate to YLL value
        yll_value = scenario_config
        for key in yll_param_path:
            if isinstance(yll_value, dict) and key in yll_value:
                yll_value = yll_value[key]
            else:
                yll_value = None
                break

        if ghg_value is not None and yll_value is not None:
            network_path = results_dir / f"model_scen-{scenario_name}.nc"
            scenarios.append(
                (float(ghg_value), float(yll_value), scenario_name, network_path)
            )

    # Sort by (ghg_price, yll_value)
    scenarios.sort(key=lambda x: (x[0], x[1]))
    return scenarios


def filter_scenarios_by_suffix(
    scenarios: list[tuple[float, str, Path]],
    suffix: str = "",
) -> list[tuple[float, str, Path]]:
    """Filter 3-tuple scenarios by production-stability suffix.

    The default ``l1_cost`` uses no suffix, so ``suffix=""`` keeps only
    scenarios without any ``_l1_`` tag.
    """
    if suffix == "":
        return [(p, s, f) for p, s, f in scenarios if "_l1_" not in s]
    return [(p, s, f) for p, s, f in scenarios if s.endswith(suffix)]


def filter_combined_scenarios_by_suffix(
    scenarios: list[tuple[float, float, str, Path]],
    suffix: str = "",
) -> list[tuple[float, float, str, Path]]:
    """Filter 4-tuple combined scenarios by production-stability suffix."""
    if suffix == "":
        return [(g, y, s, f) for g, y, s, f in scenarios if "_l1_" not in s]
    return [(g, y, s, f) for g, y, s, f in scenarios if s.endswith(suffix)]


def get_log_ticks(
    values: list[float], include_zero: bool = True
) -> tuple[list[float], list[str]]:
    """Generate tick positions and labels for a log scale with round numbers.

    Creates tick marks at powers of 10 (1, 10, 100, 1000, etc.) that fall
    within the range of the provided values. When the data includes 0, it
    is placed at a position computed by ``log_scale_zero_position`` so that
    it does not collide with real data points (e.g. when 1 is an actual value).

    Args:
        values: List of parameter values (e.g., [0, 5, 14, 38, 100, 500])
        include_zero: Whether to include 0 on the log scale

    Returns:
        Tuple of (tick_positions, tick_labels)
    """
    # Filter out zeros for determining range
    nonzero_values = [v for v in values if v > 0]
    if not nonzero_values:
        return [1], ["0"] if include_zero else ([], [])

    min_val = min(nonzero_values)
    max_val = max(nonzero_values)

    # Determine the range of powers of 10
    min_power = int(np.floor(np.log10(max(min_val, 1))))
    max_power = int(np.ceil(np.log10(max_val)))

    ticks = []
    labels = []

    # Compute the zero position from the data spacing
    has_zero = include_zero and 0 in values
    zero_pos = None
    if has_zero:
        zero_pos = log_scale_zero_position(np.array(values, dtype=float))
        ticks.append(zero_pos)
        labels.append("0")

    # Add powers of 10, skipping any that collide with the zero position
    for power in range(min_power, max_power + 1):
        tick_val = 10**power
        if tick_val >= min_val and tick_val <= max_val * 1.1:
            if zero_pos is not None and tick_val == zero_pos:
                continue
            ticks.append(tick_val)
            # Format label
            if tick_val >= 1000:
                labels.append(f"{tick_val // 1000}k")
            else:
                labels.append(str(int(tick_val)))

    # Add the max value as an endpoint tick if it's significantly beyond the
    # last power-of-10 tick (otherwise the rightmost data region is unlabeled)
    real_ticks = [t for t in ticks if t != zero_pos]
    if real_ticks and max_val > max(real_ticks) * 1.5:
        ticks.append(max_val)
        if max_val >= 1000:
            labels.append(f"{int(max_val) // 1000}k")
        else:
            labels.append(str(int(max_val)))

    # Make sure we have at least the endpoints if no powers of 10 fall in range
    n_real_ticks = len(ticks) - (1 if has_zero else 0)
    if n_real_ticks == 0:
        # Add min and max values as ticks
        for val in [min_val, max_val]:
            if val not in ticks:
                ticks.append(val)
                if val >= 1000:
                    labels.append(f"{int(val) // 1000}k")
                else:
                    labels.append(str(int(val)))
        # Sort by tick value
        combined = sorted(zip(ticks, labels))
        ticks = [t for t, _ in combined]
        labels = [label for _, label in combined]

    return ticks, labels


# -----------------------------------------------------------------------------
# Data loading utilities
# -----------------------------------------------------------------------------


def is_cache_valid(cache_path: Path, source_files: list[Path]) -> bool:
    """Check if cache file is newer than all source files."""
    if not cache_path.exists():
        return False
    cache_mtime = cache_path.stat().st_mtime
    return all(sf.stat().st_mtime <= cache_mtime for sf in source_files)


def load_population(project_root: Path, config_name: str) -> float:
    """Load total global population from processing CSV.

    Args:
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')

    Returns:
        Total global population
    """
    pop_path = project_root / "processing" / config_name / "population.csv"
    if not pop_path.exists():
        raise FileNotFoundError(
            f"Population file not found: {pop_path}. "
            f"Run the workflow to generate processing files first."
        )
    pop_df = pd.read_csv(pop_path)
    return pop_df["population"].sum()


def extract_param_value(scenario_name: str, prefix: str) -> float | None:
    """Extract a numeric parameter value from a scenario name.

    Args:
        scenario_name: e.g. 'yll_5000' or 'ghg_100'
        prefix: e.g. 'yll' or 'ghg'

    Returns:
        The numeric value, or None if pattern doesn't match
    """
    if scenario_name == "baseline":
        return None
    match = re.match(rf"{prefix}_(\d+)", scenario_name)
    if match:
        return float(match.group(1))
    return None


def extract_combined_param_value(scenario_name: str) -> tuple[float, float] | None:
    """Extract GHG price and YLL value from combined scenario name.

    Args:
        scenario_name: e.g. 'ghg_yll_100' (ghg=100, yll=40000)

    Returns:
        Tuple of (ghg_price, yll_value), or None if pattern doesn't match
    """
    if scenario_name == "baseline":
        return None
    match = re.match(r"ghg_yll_(\d+)", scenario_name)
    if match:
        ghg_price = float(match.group(1))
        yll_value = ghg_price * 400  # Fixed ratio (ghg=500 → yll=200000)
        return (ghg_price, yll_value)
    return None


def load_food_to_group(project_root: Path) -> dict[str, str]:
    """Load food to group mapping from CSV."""
    food_groups_df = pd.read_csv(project_root / "data" / "curated" / "food_groups.csv")
    return dict(zip(food_groups_df["food"], food_groups_df["group"]))


# -----------------------------------------------------------------------------
# Data loading from workflow analysis outputs
# -----------------------------------------------------------------------------


def load_consumption_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load consumption data from extract_statistics rule outputs.

    Reads pre-computed statistics CSVs from the workflow analysis outputs.

    Requires running the Snakemake extract_statistics rule first:
        tools/smk --configfile config/{name}.yaml -- results/{name}/analysis/scen-{scenario}/food_group_consumption.csv

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and food groups as columns (kcal/person/day)
    """
    population = load_population(project_root, config_name)
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir
            / "analysis"
            / f"scen-{scenario_name}"
            / "food_group_consumption.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Statistics file not found: {csv_path}. "
                f"Run the extract_statistics rule first."
            )

        df = pd.read_csv(csv_path)

        # Sum cal_pj across countries for each food group
        total_pj = df.groupby("food_group")["cal_pj"].sum()

        # Convert to global kcal/person/day
        kcal_per_person_day = total_pj * PJ_TO_KCAL / (population * DAYS_PER_YEAR)

        data[param_value] = kcal_per_person_day

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def load_objective_from_analysis(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load objective breakdown from precomputed analysis outputs.

    Reads objective_breakdown.csv files produced by the extract_objective_breakdown
    Snakemake rule.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'sensitivity')
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and cost categories as columns (bn USD)
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir
            / "analysis"
            / f"scen-{scenario_name}"
            / "objective_breakdown.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Objective breakdown file not found: {csv_path}. "
                f"Run the extract_objective_breakdown rule first."
            )

        row = pd.read_csv(csv_path).iloc[0]
        data[param_value] = row

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def load_ghg_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load GHG emissions data from extract_ghg_attribution rule outputs.

    Reads pre-computed ghg_attribution.csv files from the workflow analysis outputs.

    Requires running the Snakemake extract_ghg_attribution rule first.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and food groups as columns (in GtCO2eq)
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "ghg_attribution.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"GHG attribution file not found: {csv_path}. "
                f"Run the extract_ghg_attribution rule first."
            )

        df = pd.read_csv(csv_path)

        # Compute total emissions per food_group:
        # consumption_mt * ghg_kgco2e_per_kg = MtCO2e (since kgCO2e/kg = MtCO2e/Mt)
        df["ghg_mtco2e"] = df["consumption_mt"] * df["ghg_kgco2e_per_kg"]

        # Sum by food_group and convert to GtCO2e
        totals = df.groupby("food_group")["ghg_mtco2e"].sum() / 1000

        data[param_value] = totals

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def load_net_emissions(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.Series:
    """Load total net GHG emissions from extract_net_emissions rule outputs.

    Reads pre-computed net_emissions.csv files and returns the total net
    emissions (including negative emissions from spared land sequestration).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)

    Returns:
        Series with param_value as index and net emissions in GtCO2eq as values.
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "net_emissions.csv"
        )
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Net emissions file not found: {csv_path}. "
                f"Run the extract_net_emissions rule first."
            )

        df = _load_net_emissions_csv(csv_path)
        # Sum all sources across gases and convert MtCO2eq to GtCO2eq
        data[param_value] = df["mtco2eq"].sum() / 1000

    result = pd.Series(data, name="net_ghg_gtco2eq")
    result.index.name = param_name
    return result.sort_index()


def load_objective_from_statistics(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
    constant_health_value: float = 10000,
    constant_ghg_price: float = 100,
) -> pd.DataFrame:
    """Load objective breakdown from extract_objective_breakdown rule outputs.

    Reads pre-computed objective_breakdown.csv, ghg_attribution_totals.csv, and
    health_totals.csv files. Recomputes health/GHG costs at constant prices
    for comparability across scenarios with different price assumptions.

    Requires running the Snakemake analysis rules first (extract_objective_breakdown,
    extract_ghg_attribution, extract_health_impacts).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg', 'yll', 'ghg_yll')
        param_name: Name for the parameter (used as index name)
        constant_health_value: USD/YLL for health burden calculation
        constant_ghg_price: USD/tCO2eq for GHG cost calculation

    Returns:
        DataFrame with param_value as index and cost categories as columns (billion USD)
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        analysis_dir = results_dir / "analysis" / f"scen-{scenario_name}"

        # Load objective breakdown
        obj_path = analysis_dir / "objective_breakdown.csv"
        if not obj_path.exists():
            raise FileNotFoundError(
                f"Objective breakdown file not found: {obj_path}. "
                f"Run the extract_objective_breakdown rule first."
            )
        obj_df = pd.read_csv(obj_path)

        # Start with the breakdown categories (excluding health/GHG cost columns
        # which we recompute at constant prices)
        row = {}
        skip_cols = {"health_burden", "ghg_cost"}
        for col in obj_df.columns:
            if col not in skip_cols:
                row[col] = obj_df[col].iloc[0]

        # Load GHG attribution totals and compute cost at constant price
        ghg_totals_path = analysis_dir / "ghg_attribution_totals.csv"
        if ghg_totals_path.exists():
            ghg_totals_df = pd.read_csv(ghg_totals_path)
            ghg_mtco2eq = ghg_totals_df["ghg_mtco2eq"].sum()
            # MtCO2eq * USD/tCO2eq * 1e6 t/Mt * 1e-9 bn/USD = MtCO2eq * USD/tCO2eq * 1e-3
            ghg_cost_bnusd = ghg_mtco2eq * constant_ghg_price * 1e-3
            row["GHG cost"] = ghg_cost_bnusd

        # Load health totals and compute cost at constant price
        health_totals_path = analysis_dir / "health_totals.csv"
        if health_totals_path.exists():
            health_totals_df = pd.read_csv(health_totals_path)
            health_myll = health_totals_df["yll_myll"].sum()
            # MYLL * USD/YLL * 1e6 YLL/MYLL * 1e-9 bn/USD = MYLL * USD/YLL * 1e-3
            health_cost_bnusd = health_myll * constant_health_value * 1e-3
            row["Health burden"] = health_cost_bnusd

        data[param_value] = pd.Series(row)

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name

    # Rename columns to human-readable format
    column_map = {
        "crop_production": "Crop production",
        "trade": "Trade",
        "fertilizer": "Fertilizer (synthetic)",
        "processing": "Processing",
        "consumption": "Consumption",
        "animal_production": "Animal production",
        "feed_conversion": "Feed conversion",
        "consumer_values": "Consumer values",
        "biomass_exports": "Biomass exports",
        "biomass_routing": "Biomass routing",
        "slack_penalties": "Slack penalties",
        "resource_supply": "Resource supply",
        "nutrient_tracking": "Nutrient tracking",
    }
    result = result.rename(columns=column_map)

    return result.sort_index()


# -----------------------------------------------------------------------------
# Health cost attribution loader
# -----------------------------------------------------------------------------


def load_health_attribution_from_analysis(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load health attribution from pre-computed analysis CSVs.

    Reads ``health_attribution.csv`` for each scenario and aggregates
    by food_group (summing ``yll_myll`` across clusters and causes).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Root of the project (e.g., ``Path("..")``)
        config_name: Config name (e.g., ``"sensitivity"``)
        param_name: Name for the parameter index column

    Returns:
        DataFrame with param_value as index and food groups as columns (MYLL)
    """
    results = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            project_root
            / "results"
            / config_name
            / "analysis"
            / f"scen-{scenario_name}"
            / "health_attribution.csv"
        )
        df = pd.read_csv(csv_path)
        by_fg = df.groupby("food_group")["yll_myll"].sum()
        results[param_value] = by_fg

    out = pd.DataFrame(results).T.fillna(0)
    out.index.name = param_name
    return out.sort_index()


# Gas display names with subscripts
GAS_DISPLAY = {
    "CO2": "CO\u2082",
    "CH4": "CH\u2084",
    "N2O": "N\u2082O",
    "co2": "CO\u2082",
    "ch4": "CH\u2084",
    "n2o": "N\u2082O",
}

# Gas-specific colormaps (matching plot_emissions_breakdown.py)
GAS_CMAPS = {
    "CO2": "Greys",
    "CH4": "Greens",
    "N2O": "Oranges",
}

# Pretty names for gases
PRETTY_NAMES_GAS = {
    GAS_DISPLAY["co2"]: GAS_DISPLAY["co2"],
    GAS_DISPLAY["ch4"]: GAS_DISPLAY["ch4"],
    GAS_DISPLAY["n2o"]: GAS_DISPLAY["n2o"],
}

# Pretty names for emission sources (matching categorize_emission_carrier output)
PRETTY_NAMES_EMISSIONS = {
    "Carbon sequestration": "Carbon sequestration",
    "Crop residue incorporation": "Crop residues",
    "Enteric fermentation": "Enteric fermentation",
    "Enteric fermentation & Manure management": "Enteric ferm.\n& Manure mgmt",
    "Land Use Change": "Land use change",
    "Manure management & application": "Manure mgmt\n& application",
    "Manure: managed systems": "Manure: managed",
    "Manure: pasture deposition": "Manure: pasture",
    "Rice cultivation": "Rice cultivation",
    "Synthetic fertilizer application": "Synthetic fertilizer",
}


def _load_net_emissions_csv(csv_path: Path) -> pd.DataFrame:
    """Load ``net_emissions.csv`` using the exact analysis output schema."""
    df = pd.read_csv(csv_path)
    expected_cols = {"gas", "source", "mtco2eq"}
    if set(df.columns) != expected_cols:
        raise ValueError(
            f"Expected columns {sorted(expected_cols)} in {csv_path}, "
            f"got {list(df.columns)}"
        )

    normalized = df.copy()
    normalized["gas"] = normalized["gas"].astype(str).str.lower()
    normalized["mtco2eq"] = normalized["mtco2eq"].astype(float)
    return normalized


def load_net_emissions_by_gas(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
) -> pd.DataFrame:
    """Load per-gas net emissions (CO2, CH4, N2O) from net_emissions.csv across scenarios.

    Each gas value is already in MtCO2eq (GWP-adjusted). Returns in GtCO2eq.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)

    Returns:
        DataFrame with param_value as index and gas names (CO2, CH4, N2O) as columns,
        in GtCO2eq. Rows with missing CSV files are skipped.
    """
    results_dir = project_root / "results" / config_name

    data = {}
    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "net_emissions.csv"
        )
        if not csv_path.exists():
            continue

        df = _load_net_emissions_csv(csv_path)
        row = {}
        for gas in ["co2", "ch4", "n2o"]:
            gas_total = df.loc[df["gas"] == gas, "mtco2eq"].sum()
            # Convert MtCO2eq to GtCO2eq
            row[GAS_DISPLAY[gas]] = gas_total / 1000
        data[param_value] = row

    if not data:
        return pd.DataFrame()

    result = pd.DataFrame(data).T.fillna(0)
    result.index.name = param_name
    return result.sort_index()


def load_emissions_by_source(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str = "param_value",
    **_kwargs,
) -> dict[str, pd.DataFrame]:
    """Load per-source emissions breakdown for each gas across scenarios.

    Reads the source-level ``net_emissions.csv`` produced by ``analyze_model``
    (columns: gas, source, mtco2eq).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)

    Returns:
        Dict mapping gas name (CO2, CH4, N2O) to DataFrames with param_value
        as index and emission sources as columns, in GtCO2eq.
    """
    results_dir = project_root / "results" / config_name

    gas_data: dict[str, dict[float, dict[str, float]]] = {
        "CO2": {},
        "CH4": {},
        "N2O": {},
    }

    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "net_emissions.csv"
        )
        if not csv_path.exists():
            continue

        df = _load_net_emissions_csv(csv_path)
        for gas in ["co2", "ch4", "n2o"]:
            gas_rows = df[df["gas"] == gas]
            gas_key = gas.upper()
            # Convert MtCO2eq to GtCO2eq
            gas_data[gas_key][param_value] = {
                row["source"]: row["mtco2eq"] / 1000 for _, row in gas_rows.iterrows()
            }

    result = {}
    for gas in ["CO2", "CH4", "N2O"]:
        if not gas_data[gas]:
            result[gas] = pd.DataFrame()
            continue
        df = pd.DataFrame(gas_data[gas]).T.fillna(0)
        df.index.name = param_name
        df = df.sort_index()
        result[gas] = df

    return result


# -----------------------------------------------------------------------------
# Land use change breakdown utilities
# -----------------------------------------------------------------------------

# Display names for LUC categories
LUC_TYPE_DISPLAY = {
    "Cropland expansion": "Cropland expansion",
    "Pasture expansion": "Pasture expansion",
    "Cropland sparing": "Cropland sparing",
    "Grassland sparing (convertible)": "Grassland sparing\n(convertible)",
    "Grassland sparing (marginal)": "Grassland sparing\n(marginal)",
}

# Continent display order (roughly by LUC importance)
CONTINENT_ORDER = [
    "Africa",
    "Americas",
    "Asia",
    "Europe",
    "Oceania",
]


def load_luc_breakdown(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str,
    groupby: str,
    quantity: str = "emissions",
    **_kwargs,
) -> pd.DataFrame:
    """Load LUC breakdown across scenarios from analysis CSVs.

    Reads ``luc_breakdown.csv`` produced by ``analyze_model`` (columns:
    groupby, category, emissions_mtco2, area_mha).

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)
        groupby: "continent" or "land_type"
        quantity: "emissions" (GtCO2) or "area" (Mha)

    Returns:
        DataFrame with param_value as index and categories as columns.
        Units: GtCO2 for emissions, Mha for area.
    """
    results_dir = project_root / "results" / config_name
    value_col = "emissions_mtco2" if quantity == "emissions" else "area_mha"

    data: dict[float, dict[str, float]] = {}

    for param_value, scenario_name, _ in scenarios:
        csv_path = (
            results_dir / "analysis" / f"scen-{scenario_name}" / "luc_breakdown.csv"
        )
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path)
        rows = df[df["groupby"] == groupby]
        if quantity == "emissions":
            # Convert MtCO2 to GtCO2
            data[param_value] = {
                row["category"]: row[value_col] / 1000 for _, row in rows.iterrows()
            }
        else:
            data[param_value] = {
                row["category"]: row[value_col] for _, row in rows.iterrows()
            }

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data).T.fillna(0)
    df.index.name = param_name
    return df.sort_index()


# -----------------------------------------------------------------------------
# Feed breakdown utilities
# -----------------------------------------------------------------------------

# Re-export constants from the plotting module for notebook use
FEED_CATEGORY_LABELS = {
    "ruminant_forage": "Grass & fodder",
    "ruminant_roughage": "Crop residues",
    "ruminant_grain": "Grains",
    "ruminant_protein": "Oilseed cakes",
    "monogastric_grain": "Grains",
    "monogastric_low_quality": "By-products",
    "monogastric_protein": "Oilseed cakes",
}

FEED_ORDER = [
    "Grass & leaves",
    "Crop residues",
    "Fodder crops",
    "Oilseed cakes",
    "By-products",
    "Grains",
]

FEED_COLORS = {
    "Grass & leaves": "#4f9d69",
    "Crop residues": "#8c6b4f",
    "Fodder crops": "#a6d96a",
    "Oilseed cakes": "#b8de6f",
    "By-products": "#7b6ba8",
    "Grains": "#d95f02",
}

PRODUCT_TO_ANIMAL = {
    "meat-cattle": "Cattle",
    "dairy": "Cattle",
    "meat-pig": "Pigs",
    "meat-chicken": "Chicken",
    "eggs": "Chicken",
    "meat-sheep": "Sheep",
    "meat-goat": "Goats",
    "meat-buffalo": "Buffalo",
    "dairy-buffalo": "Buffalo",
    "milk-sheep": "Sheep",
    "milk-goat": "Goats",
    "milk-buffalo": "Buffalo",
}

ANIMAL_COLORS = {
    "Cattle": "#d62728",
    "Pigs": "#ff7f0e",
    "Chicken": "#2ca02c",
    "Sheep": "#1f77b4",
    "Goats": "#9467bd",
    "Buffalo": "#8c564b",
}


def _get_crop_group_style() -> tuple[dict[str, str], dict[str, tuple[float, ...]]]:
    """Return crop-to-group mapping and group colors used by crop production map.

    The mapping and palette mirror
    `workflow/scripts/plotting/plot_crop_production_map.py`.
    """
    crop_to_group = {
        # Cereals
        "wheat": "Cereals",
        "dryland-rice": "Cereals",
        "wetland-rice": "Cereals",
        "maize": "Cereals",
        "barley": "Cereals",
        "oat": "Cereals",
        "rye": "Cereals",
        "sorghum": "Cereals",
        "buckwheat": "Cereals",
        "foxtail-millet": "Cereals",
        "pearl-millet": "Cereals",
        # Legumes
        "soybean": "Legumes",
        "dry-pea": "Legumes",
        "chickpea": "Legumes",
        "cowpea": "Legumes",
        "gram": "Legumes",
        "phaseolus-bean": "Legumes",
        "pigeonpea": "Legumes",
        # Roots & tubers
        "white-potato": "Roots & tubers",
        "sweet-potato": "Roots & tubers",
        "cassava": "Roots & tubers",
        "yam": "Roots & tubers",
        # Vegetables
        "tomato": "Vegetables",
        "carrot": "Vegetables",
        "onion": "Vegetables",
        "cabbage": "Vegetables",
        # Fruits
        "banana": "Fruits",
        "citrus": "Fruits",
        "coconut": "Fruits",
        # Oilseeds
        "sunflower": "Oilseeds",
        "rapeseed": "Oilseeds",
        "groundnut": "Oilseeds",
        "sesame": "Oilseeds",
        "oil-palm": "Oilseeds",
        "olive": "Oilseeds",
        # Sugar crops
        "sugarcane": "Sugar crops",
        "sugarbeet": "Sugar crops",
        # Feed crops
        "alfalfa": "Feed crops",
        "silage-maize": "Feed crops",
        "biomass-sorghum": "Feed crops",
        "grassland": "Feed crops",
    }

    dark2 = plt.get_cmap("Dark2").colors
    group_colors = {
        "Cereals": dark2[5],
        "Legumes": dark2[7],
        "Roots & tubers": dark2[6],
        "Vegetables": dark2[0],
        "Fruits": dark2[1],
        "Oilseeds": dark2[2],
        "Sugar crops": dark2[3],
        "Feed crops": dark2[4],
    }
    return crop_to_group, group_colors


CROP_TO_GROUP, CROP_GROUP_COLORS = _get_crop_group_style()


def aggregate_crop_metrics_by_group(
    yield_df: pd.DataFrame,
    production_df: pd.DataFrame,
    crop_to_group: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate crop-level yield/production to crop groups.

    Group yield is computed as production-weighted mean crop yield:
    sum(yield * production) / sum(production).

    Args:
        yield_df: Crop-level average yields (Mt/Mha), indexed by scenario value
        production_df: Crop-level production (Mt), indexed by scenario value
        crop_to_group: Optional mapping from crop name to group label

    Returns:
        Tuple `(yield_by_group, production_by_group)` with group labels as
        columns and the same index as inputs.
    """
    if yield_df.empty or production_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    if crop_to_group is None:
        crop_to_group = CROP_TO_GROUP

    common_index = yield_df.index.intersection(production_df.index)
    crops = yield_df.columns.union(production_df.columns)

    y = yield_df.reindex(index=common_index, columns=crops, fill_value=0.0)
    p = production_df.reindex(index=common_index, columns=crops, fill_value=0.0)

    group_cols = {crop: crop_to_group.get(crop, "Other") for crop in crops}

    # Aggregate production by group
    p_group = p.T.groupby(group_cols).sum().T

    # Production-weighted mean yield by group
    yp_group = (y * p).T.groupby(group_cols).sum().T
    y_group = yp_group.div(p_group.where(p_group > 0.0)).replace(
        [np.inf, -np.inf], np.nan
    )
    y_group = y_group.fillna(0.0)

    # Use map color order first, then any unexpected groups alphabetically
    preferred_order = [g for g in CROP_GROUP_COLORS if g in p_group.columns]
    extras = sorted([g for g in p_group.columns if g not in preferred_order])
    col_order = preferred_order + extras

    p_group = p_group.reindex(columns=col_order, fill_value=0.0)
    y_group = y_group.reindex(columns=col_order, fill_value=0.0)

    return y_group, p_group


def load_feed_breakdown(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str,
    groupby: str = "feed_category",
    **_kwargs,
) -> pd.DataFrame:
    """Load feed use breakdown across scenarios from analysis CSVs.

    Reads ``feed_by_category.csv`` or ``feed_by_animal.csv`` produced by
    ``analyze_model``.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)
        groupby: "feed_category" or "animal"

    Returns:
        DataFrame with param_value as index and categories as columns, in Gt DM.
    """
    results_dir = project_root / "results" / config_name
    if groupby == "feed_category":
        csv_name = "feed_by_category.csv"
        key_col = "category"
    else:
        csv_name = "feed_by_animal.csv"
        key_col = "animal"

    data: dict[float, dict[str, float]] = {}

    for param_value, scenario_name, _ in scenarios:
        csv_path = results_dir / "analysis" / f"scen-{scenario_name}" / csv_name
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path)
        # Convert Mt to Gt
        data[param_value] = {
            row[key_col]: row["mt_dm"] / 1000 for _, row in df.iterrows()
        }

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data).T.fillna(0)
    df.index.name = param_name
    return df.sort_index()


def load_crop_yield_and_production(
    scenarios: list[tuple[float, str, Path]],
    project_root: Path,
    config_name: str,
    param_name: str,
    include_grassland: bool = False,
    **_kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load crop-level average yield and production across scenarios.

    Reads ``crop_production.csv`` and ``land_use.csv`` from the analysis
    outputs and derives average yield per crop.

    Args:
        scenarios: List of (param_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config
        param_name: Name for the parameter (used as index name)
        include_grassland: Include grassland pseudo-crop when True

    Returns:
        Tuple of DataFrames (yield_df, production_df), each with param_value
        as index and crop names as columns. Units: Mt/Mha and Mt.
    """
    results_dir = project_root / "results" / config_name

    yield_data: dict[float, dict[str, float]] = {}
    production_data: dict[float, dict[str, float]] = {}

    for param_value, scenario_name, _ in scenarios:
        analysis_dir = results_dir / "analysis" / f"scen-{scenario_name}"
        prod_path = analysis_dir / "crop_production.csv"
        land_path = analysis_dir / "land_use.csv"
        if not prod_path.exists() or not land_path.exists():
            continue

        prod_df = pd.read_csv(prod_path)
        land_df = pd.read_csv(land_path)

        production = prod_df.groupby("crop")["production_mt"].sum()
        land = land_df.groupby("crop")["area_mha"].sum()

        if not include_grassland:
            production = production.drop(index="grassland", errors="ignore")
            land = land.drop(index="grassland", errors="ignore")

        crops = production.index.union(land.index)
        production = production.reindex(crops, fill_value=0.0)
        land = land.reindex(crops, fill_value=0.0)

        avg_yield = production.div(land.where(land > 0)).replace(
            [np.inf, -np.inf], np.nan
        )
        avg_yield = avg_yield.fillna(0.0)

        production_data[param_value] = production.to_dict()
        yield_data[param_value] = avg_yield.to_dict()

    if not production_data:
        return pd.DataFrame(), pd.DataFrame()

    production_out = pd.DataFrame(production_data).T.fillna(0.0)
    yield_out = pd.DataFrame(yield_data).T.fillna(0.0)

    production_out.index.name = param_name
    yield_out.index.name = param_name

    production_out = production_out.sort_index()
    yield_out = yield_out.sort_index()

    crop_order = production_out.mean().sort_values(ascending=False).index
    production_out = production_out[crop_order]
    yield_out = yield_out.reindex(columns=crop_order, fill_value=0.0)

    return yield_out, production_out


def plot_stacked_emissions(
    df: pd.DataFrame,
    colors: dict,
    ax: plt.Axes,
    xlabel: str,
    ylabel: str,
    panel_label: str,
    x_ticks: list[float],
    x_ticklabels: list[str],
    pretty_names: dict | None = None,
    min_height_for_label: float = 0.3,
):
    """Stacked area plot handling both positive and negative values.

    Positive parts of each column stack upward from zero, negative parts stack
    downward.  This is needed for emissions data where CO2 can include both
    land-use-change emissions (positive) and carbon sequestration (negative).

    Args:
        df: DataFrame with parameter values as index and groups as columns
        colors: Dict mapping group names to colors
        ax: Matplotlib axes to plot on
        xlabel: X-axis label
        ylabel: Y-axis label
        panel_label: Panel label (e.g., 'a')
        x_ticks: X-axis tick positions
        x_ticklabels: X-axis tick labels
        pretty_names: Custom pretty names dict
        min_height_for_label: Minimum height to show a label (in data units)
    """
    if pretty_names is None:
        pretty_names = {}

    x_values = df.index.values
    groups = df.columns.tolist()

    zero_pos = log_scale_zero_position(x_values)
    x_plot = np.where(x_values == 0, zero_pos, x_values)

    tick_max = max(x_ticks) if x_ticks else 0
    tick_min = min(x_ticks) if x_ticks else zero_pos
    # Extend x range to cover all tick positions (the "0" tick may differ
    # from the data zero position when only a subset of scenarios is solved)
    x_min = min(zero_pos, tick_min)
    x_max = max(x_plot.max(), tick_max)
    x_smooth = np.logspace(np.log10(x_min), np.log10(x_max), 200)

    y_smooth = {}
    for group in groups:
        y_smooth[group] = np.interp(
            np.log10(x_smooth), np.log10(x_plot), df[group].values
        )

    # Build positive and negative stacks
    y_pos_stack = [np.zeros(len(x_smooth))]
    y_neg_stack = [np.zeros(len(x_smooth))]
    pos_entries = []  # (group, bottom_array, top_array)
    neg_entries = []

    for group in groups:
        y = y_smooth[group]
        y_pos = np.maximum(y, 0)
        y_neg = np.minimum(y, 0)

        if np.any(y_pos > 1e-9):
            bottom = y_pos_stack[-1].copy()
            top = bottom + y_pos
            y_pos_stack.append(top)
            pos_entries.append((group, bottom, top))
            ax.fill_between(
                x_smooth,
                bottom,
                top,
                color=colors.get(group, "#999999"),
                alpha=0.8,
                edgecolor="white",
                linewidth=0.5,
            )

        if np.any(y_neg < -1e-9):
            top = y_neg_stack[-1].copy()
            bottom = top + y_neg
            y_neg_stack.append(bottom)
            neg_entries.append((group, bottom, top))
            ax.fill_between(
                x_smooth,
                bottom,
                top,
                color=colors.get(group, "#999999"),
                alpha=0.8,
                edgecolor="white",
                linewidth=0.5,
            )

    ax.axhline(0, color="black", linewidth=0.5)

    # Labels — for each group, only label the part (pos or neg) with
    # the larger mean area, to avoid duplicate labels when a group
    # crosses zero.
    bbox_style = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }
    label_fontsize = FONTSIZE_TICK_LABEL
    log_x_min, log_x_max = np.log10(x_min), np.log10(x_max)
    margin_frac = 0.15
    log_margin = (log_x_max - log_x_min) * margin_frac

    # Compute mean area for each (group, sign) entry to pick the dominant one
    group_best_area = {}  # group -> (mean_area, entry_tuple)
    for group, y_bottom, y_top in pos_entries + neg_entries:
        mean_area = np.mean(np.abs(y_top - y_bottom))
        if group not in group_best_area or mean_area > group_best_area[group][0]:
            group_best_area[group] = (mean_area, (group, y_bottom, y_top))

    for group, (_mean_area, (_, y_bottom, y_top)) in group_best_area.items():
        heights = np.abs(y_top - y_bottom)
        if heights.max() < min_height_for_label:
            continue
        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]
        log_x_pos = np.clip(
            np.log10(x_pos), log_x_min + log_margin, log_x_max - log_margin
        )
        x_pos = 10**log_x_pos
        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_pos = (y_bottom[idx] + y_top[idx]) / 2
        label_text = pretty_names.get(group, PRETTY_NAMES.get(group, group))
        ax.text(
            x_pos,
            y_pos,
            label_text,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    ax.set_xscale("log")
    ax.set_xlabel(xlabel, fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel(ylabel, fontsize=FONTSIZE_AXIS_LABEL)

    ax.text(
        -0.10,
        1.05,
        panel_label,
        transform=ax.transAxes,
        fontsize=FONTSIZE_PANEL_LABEL,
        fontweight="bold",
        va="top",
        ha="left",
    )

    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_ticklabels)
    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)
    ax.set_xlim(x_min, x_max)

    ax.grid(True, alpha=0.3, which="both")
    ax.set_axisbelow(True)


# -----------------------------------------------------------------------------
# Data preparation utilities
# -----------------------------------------------------------------------------


def aggregate_food_groups(
    df: pd.DataFrame, min_peak_share: float = 0.0
) -> pd.DataFrame:
    """Aggregate and optionally filter food groups for plotting.

    Combines fruits+vegetables and eggs+poultry into aggregate groups. Optionally
    drops tiny groups whose peak absolute value is below a fraction of the
    dominant group's peak.

    Args:
        df: Input DataFrame with food groups as columns.
        min_peak_share: Minimum share of the largest peak absolute value for a
            group to be kept (e.g., 0.01 keeps groups >=1% of the max). Must be
            in [0, 1).
    """
    if not 0 <= min_peak_share < 1:
        raise ValueError(f"min_peak_share must be in [0, 1), got {min_peak_share}")

    df_plot = df.copy()

    if "fruits" in df_plot.columns and "vegetables" in df_plot.columns:
        df_plot["fruits_vegetables"] = df_plot["fruits"] + df_plot["vegetables"]
        df_plot = df_plot.drop(columns=["fruits", "vegetables"])

    if "eggs" in df_plot.columns and "poultry" in df_plot.columns:
        df_plot["eggs_poultry"] = df_plot["eggs"] + df_plot["poultry"]
        df_plot = df_plot.drop(columns=["eggs", "poultry"])

    if min_peak_share > 0 and not df_plot.empty:
        peak_by_group = df_plot.abs().max(axis=0)
        global_peak = peak_by_group.max()
        if global_peak > 0:
            keep_groups = peak_by_group[
                peak_by_group >= global_peak * min_peak_share
            ].index
            df_plot = df_plot.loc[:, keep_groups]

    return df_plot


def prepare_objective_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare objective data: rename to display names, aggregate, and order categories."""
    df_obj = df.copy()

    # Rename snake_case columns from precomputed CSVs to display names
    snake_to_display = {
        "crop_production": "Crop production",
        "trade": "Trade",
        "fertilizer": "Fertilizer",
        "processing": "Processing",
        "consumption": "Consumption",
        "animal_production": "Animal production",
        "feed_conversion": "Feed conversion",
        "consumer_values": "Consumer values",
        "biomass_exports": "Biomass exports",
        "biomass_routing": "Biomass routing",
        "health_burden": "Health burden",
        "ghg_cost": "GHG cost",
        "slack_penalties": "Slack penalties",
        "production_stability": "Production stability",
        "resource_supply": "Resource supply",
        "nutrient_tracking": "Nutrient tracking",
        "emissions_aggregation": "Emissions aggregation",
        "land_use": "Land use",
        "water": "Water",
    }
    df_obj = df_obj.rename(
        columns={k: v for k, v in snake_to_display.items() if k in df_obj.columns}
    )

    # Merge fertilizer into crop production
    if "Fertilizer" in df_obj.columns and "Crop production" in df_obj.columns:
        df_obj["Crop production"] = df_obj["Crop production"] + df_obj["Fertilizer"]
        df_obj = df_obj.drop(columns=["Fertilizer"])

    # Drop negligible categories (max absolute value < 1 bn USD)
    significant = df_obj.columns[df_obj.abs().max() >= 1.0]
    df_obj = df_obj[significant]

    priority_order = ["Crop production", "Trade"]
    other_cats = [c for c in df_obj.columns if c not in priority_order]
    other_cats_sorted = (
        df_obj[other_cats].mean().sort_values(ascending=False).index.tolist()
    )
    cat_order = [c for c in priority_order if c in df_obj.columns] + other_cats_sorted

    return df_obj[cat_order]


def assign_food_colors(df: pd.DataFrame) -> dict:
    """Assign tab20 colors to food groups based on consumption at minimum x-value."""
    cmap = plt.colormaps["tab20"]
    min_val = df.index.min()
    group_order = df.loc[min_val].sort_values(ascending=False).index.tolist()
    return {group: cmap(i) for i, group in enumerate(group_order)}


# -----------------------------------------------------------------------------
# Plotting functions
# -----------------------------------------------------------------------------


def set_dual_xaxis_labels(
    ax: plt.Axes,
    x_ticks: list[float],
    ghg_values: list[float],
    yll_values: list[float],
    ghg_color: str = "darkgreen",
    yll_color: str = "darkblue",
    fontsize: int = FONTSIZE_TICK_LABEL,
):
    """Set up dual-colored x-axis tick labels showing both GHG price and YLL value.

    Args:
        ax: Matplotlib axes
        x_ticks: X-axis tick positions
        ghg_values: GHG price values for each tick
        yll_values: YLL values for each tick
        ghg_color: Color for GHG labels
        yll_color: Color for YLL labels
        fontsize: Font size for tick labels
    """
    ax.set_xticks(x_ticks)
    ax.set_xticklabels([])  # Clear default labels

    # Get axis transform for positioning
    trans = ax.get_xaxis_transform()

    # Add colored labels below axis
    for x, ghg, yll in zip(x_ticks, ghg_values, yll_values):
        # Format values nicely
        ghg_str = f"{int(ghg)}" if ghg < 1000 else f"{int(ghg / 1000)}k"
        yll_str = f"{int(yll)}" if yll < 1000 else f"{int(yll / 1000)}k"

        # GHG label (top, dark green)
        ax.text(
            x,
            -0.02,
            ghg_str,
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            color=ghg_color,
            fontweight="bold",
        )
        # YLL label (bottom, dark blue)
        ax.text(
            x,
            -0.08,
            yll_str,
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            color=yll_color,
            fontweight="bold",
        )


def set_dual_xlabel(
    ax: plt.Axes,
    ghg_color: str = "darkgreen",
    yll_color: str = "darkblue",
    fontsize: int = FONTSIZE_AXIS_LABEL,
):
    """Set dual-colored x-axis label for combined GHG/YLL sensitivity.

    Args:
        ax: Matplotlib axes
        ghg_color: Color for GHG part
        yll_color: Color for YLL part
        fontsize: Font size
    """
    # Use a two-line xlabel with colored text
    ax.set_xlabel("")  # Clear default

    ax.text(
        0.5,
        -0.18,
        "GHG price [USD/tCO2eq]",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=fontsize,
        color=ghg_color,
    )
    ax.text(
        0.5,
        -0.26,
        "Health value [USD/YLL]",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=fontsize,
        color=yll_color,
    )


def plot_stacked_sensitivity(
    df: pd.DataFrame,
    colors: dict,
    ax: plt.Axes,
    xlabel: str,
    ylabel: str,
    panel_label: str,
    x_ticks: list[float],
    x_ticklabels: list[str],
    label_x_positions: dict | None = None,
    label_skip: set | None = None,
    min_height_for_label: float = 30,
    y_max: float | None = None,
    pretty_names: dict | None = None,
    labels_right: bool = False,
):
    """Create a stacked area plot with logarithmic x-axis.

    Args:
        df: DataFrame with parameter values as index and groups as columns
        colors: Dict mapping group names to colors
        ax: Matplotlib axes to plot on
        xlabel: X-axis label
        ylabel: Y-axis label
        panel_label: Panel label (e.g., 'a', 'b', 'c')
        x_ticks: X-axis tick positions (in original scale, 0 maps to 1)
        x_ticklabels: X-axis tick labels
        label_x_positions: Manual x-positions for labels (optional)
        label_skip: Set of group names to skip labeling (optional)
        min_height_for_label: Minimum height to show a label
        y_max: Maximum y-axis value (optional)
        pretty_names: Custom pretty names dict (falls back to PRETTY_NAMES)
        labels_right: Place labels at the right edge of the plot (default False)
    """
    if label_x_positions is None:
        label_x_positions = {}
    if label_skip is None:
        label_skip = set()
    if pretty_names is None:
        pretty_names = PRETTY_NAMES

    x_values = df.index.values
    groups = df.columns.tolist()

    # Handle x=0 for log scale: calculate position for even spacing
    zero_pos = log_scale_zero_position(x_values)
    x_plot = np.where(x_values == 0, zero_pos, x_values)

    tick_max = max(x_ticks) if x_ticks else 0
    x_min, x_max = zero_pos, max(x_plot.max(), tick_max)
    x_smooth = np.logspace(np.log10(x_min), np.log10(x_max), 200)

    y_smooth = {}
    for group in groups:
        y_smooth[group] = np.interp(
            np.log10(x_smooth), np.log10(x_plot), df[group].values
        )

    y_stacks = [np.zeros(len(x_smooth))]
    for group in groups:
        y_stacks.append(y_stacks[-1] + y_smooth[group])

    for i, group in enumerate(groups):
        y_bottom = y_stacks[i]
        y_top = y_stacks[i + 1]
        ax.fill_between(
            x_smooth,
            y_bottom,
            y_top,
            label=group,
            color=colors[group],
            alpha=0.8,
            edgecolor="white",
            linewidth=0.5,
        )

    # Add labels
    label_fontsize = FONTSIZE_TICK_LABEL
    bbox_style = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }

    if labels_right:
        # Place labels at the right edge with vertical spreading
        right_idx = -1
        entries = []
        for i, group in enumerate(groups):
            if group in label_skip:
                continue
            if y_smooth[group].max() < min_height_for_label:
                continue
            y_bot = y_stacks[i][right_idx]
            y_top = y_stacks[i + 1][right_idx]
            y_mid = (y_bot + y_top) / 2
            label_text = pretty_names.get(group, PRETTY_NAMES.get(group, group))
            entries.append((y_mid, label_text, group))

        entries.sort(key=lambda e: e[0])

        # Spread overlapping labels apart
        total_height = y_stacks[-1].max()
        min_spacing = total_height * 0.05
        y_positions = [e[0] for e in entries]
        for _ in range(100):
            moved = False
            for j in range(1, len(y_positions)):
                gap = y_positions[j] - y_positions[j - 1]
                if gap < min_spacing:
                    push = (min_spacing - gap) / 2
                    y_positions[j - 1] -= push
                    y_positions[j] += push
                    moved = True
            # Clamp to stay within visible range
            for j in range(len(y_positions)):
                y_positions[j] = max(min_spacing / 2, y_positions[j])
                y_positions[j] = min(total_height - min_spacing / 2, y_positions[j])
            if not moved:
                break

        trans = blended_transform_factory(ax.transAxes, ax.transData)
        dot_x = 1.04
        text_x = 1.07
        for (_, label_text, group), y_pos in zip(entries, y_positions):
            ax.plot(
                dot_x,
                y_pos,
                "o",
                color=colors[group],
                markersize=3,
                transform=trans,
                clip_on=False,
            )
            ax.text(
                text_x,
                y_pos,
                label_text,
                transform=trans,
                ha="left",
                va="center",
                fontsize=label_fontsize,
                fontweight="bold",
                clip_on=False,
            )
    else:
        log_x_min, log_x_max = np.log10(x_min), np.log10(x_max)
        margin_frac = 0.15
        log_margin = (log_x_max - log_x_min) * margin_frac

        for i, group in enumerate(groups):
            if group in label_skip:
                continue

            heights = y_smooth[group]
            max_height = heights.max()

            if max_height < min_height_for_label:
                continue

            max_idx = np.argmax(heights)
            x_pos = x_smooth[max_idx]

            if group in label_x_positions:
                x_pos = label_x_positions[group]

            log_x_pos = np.log10(x_pos)
            log_x_pos = np.clip(
                log_x_pos, log_x_min + log_margin, log_x_max - log_margin
            )
            x_pos = 10**log_x_pos

            idx = np.argmin(np.abs(x_smooth - x_pos))
            y_bottom = y_stacks[i]
            y_top = y_stacks[i + 1]
            y_pos = (y_bottom[idx] + y_top[idx]) / 2

            label_text = pretty_names.get(group, PRETTY_NAMES.get(group, group))
            ax.text(
                x_pos,
                y_pos,
                label_text,
                ha="center",
                va="center",
                fontsize=label_fontsize,
                fontweight="bold",
                color="black",
                bbox=bbox_style,
            )

    ax.set_xscale("log")
    ax.set_xlabel(xlabel, fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel(ylabel, fontsize=FONTSIZE_AXIS_LABEL)

    ax.text(
        -0.10,
        1.05,
        panel_label,
        transform=ax.transAxes,
        fontsize=FONTSIZE_PANEL_LABEL,
        fontweight="bold",
        va="top",
        ha="left",
    )

    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_ticklabels)
    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)
    ax.set_xlim(x_min, x_max)
    if y_max is not None:
        ax.set_ylim(0, y_max)
    else:
        ax.set_ylim(0, None)

    ax.grid(True, alpha=0.3, which="both")
    ax.set_axisbelow(True)


def plot_objective_sensitivity(
    df: pd.DataFrame,
    ax: plt.Axes,
    xlabel: str,
    panel_label: str,
    x_ticks: list[float],
    x_ticklabels: list[str],
    health_value: float | None = None,
    ghg_price: float | None = None,
    label_x_positions: dict | None = None,
    highlight_cat: str | None = None,
):
    """Create stacked area plot for objective breakdown with positive/negative categories.

    Args:
        df: DataFrame with parameter values as index and cost categories as columns
        ax: Matplotlib axes to plot on
        xlabel: X-axis label
        panel_label: Panel label (e.g., 'c')
        x_ticks: X-axis tick positions
        x_ticklabels: X-axis tick labels
        health_value: Health value to display in note box
        ghg_price: GHG price to display in note box
        label_x_positions: Manual x-positions for labels (optional)
        highlight_cat: Category to highlight with hatching (None for no highlighting)
    """
    if label_x_positions is None:
        label_x_positions = {}

    x_values = df.index.values
    categories = df.columns.tolist()

    zero_pos = log_scale_zero_position(x_values)
    x_plot = np.where(x_values == 0, zero_pos, x_values)
    tick_max = max(x_ticks) if x_ticks else 0
    x_min, x_max = zero_pos, max(x_plot.max(), tick_max)
    x_smooth = np.logspace(np.log10(x_min), np.log10(x_max), 200)

    y_smooth = {}
    for cat in categories:
        y_smooth[cat] = np.interp(np.log10(x_smooth), np.log10(x_plot), df[cat].values)

    min_magnitude = 1.0

    crossing_cats = []
    purely_pos_cats = []
    purely_neg_cats = []

    for cat in categories:
        y = y_smooth[cat]
        max_abs = np.max(np.abs(y))
        if max_abs < min_magnitude:
            continue

        has_pos = np.any(y > 1e-6)
        has_neg = np.any(y < -1e-6)
        if has_pos and has_neg:
            crossing_cats.append(cat)
        elif has_pos:
            purely_pos_cats.append(cat)
        elif has_neg:
            purely_neg_cats.append(cat)

    y_smooth_split = {}
    cmap_obj = plt.colormaps["tab20c"]

    for cat in purely_pos_cats:
        y_smooth_split[cat] = y_smooth[cat]
    for cat in purely_neg_cats:
        y_smooth_split[cat] = y_smooth[cat]

    for cat in crossing_cats:
        y = y_smooth[cat]
        y_pos = np.maximum(y, 0)
        y_neg = np.minimum(y, 0)

        if np.max(y_pos) > min_magnitude:
            pos_name = f"{cat} (positive)"
            y_smooth_split[pos_name] = y_pos
            purely_pos_cats.append(pos_name)

        if np.min(y_neg) < -min_magnitude:
            neg_name = f"{cat} (negative)"
            y_smooth_split[neg_name] = y_neg
            purely_neg_cats.append(neg_name)

    if highlight_cat is not None and highlight_cat in purely_pos_cats:
        purely_pos_cats.remove(highlight_cat)
        purely_pos_cats.append(highlight_cat)

    split_colors = {}
    for i, cat in enumerate(purely_pos_cats):
        if cat == highlight_cat:
            split_colors[cat] = "grey"
        else:
            split_colors[cat] = cmap_obj(4 + (i % 4))
    for i, cat in enumerate(purely_neg_cats):
        split_colors[cat] = cmap_obj(i % 4)

    y_pos_stacks = [np.zeros(len(x_smooth))]
    for cat in purely_pos_cats:
        y_pos_stacks.append(y_pos_stacks[-1] + y_smooth_split[cat])

    y_neg_stacks = [np.zeros(len(x_smooth))]
    for cat in purely_neg_cats:
        y_neg_stacks.append(y_neg_stacks[-1] + y_smooth_split[cat])

    for i, cat in enumerate(purely_pos_cats):
        y_bottom = y_pos_stacks[i]
        y_top = y_pos_stacks[i + 1]
        if cat == highlight_cat:
            ax.fill_between(
                x_smooth,
                y_bottom,
                y_top,
                label=cat,
                color=split_colors[cat],
                alpha=0.4,
                edgecolor="white",
                linewidth=0.5,
                hatch="///",
            )
        else:
            ax.fill_between(
                x_smooth,
                y_bottom,
                y_top,
                label=cat,
                color=split_colors[cat],
                alpha=0.8,
                edgecolor="white",
                linewidth=0.5,
            )

    for i, cat in enumerate(purely_neg_cats):
        y_top = y_neg_stacks[i]
        y_bottom = y_neg_stacks[i + 1]
        ax.fill_between(
            x_smooth,
            y_bottom,
            y_top,
            label=cat,
            color=split_colors[cat],
            alpha=0.8,
            edgecolor="white",
            linewidth=0.5,
        )

    label_fontsize = FONTSIZE_TICK_LABEL
    bbox_style = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }

    log_x_min, log_x_max = np.log10(x_min), np.log10(x_max)
    margin_frac = 0.15
    log_margin = (log_x_max - log_x_min) * margin_frac

    for i, cat in enumerate(purely_pos_cats):
        heights = y_smooth_split[cat]
        max_height = heights.max()

        if max_height < 15:
            continue

        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]

        if cat in label_x_positions:
            x_pos = label_x_positions[cat]

        log_x_pos = np.log10(x_pos)
        log_x_pos = np.clip(log_x_pos, log_x_min + log_margin, log_x_max - log_margin)
        x_pos = 10**log_x_pos

        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_bottom = y_pos_stacks[i]
        y_top = y_pos_stacks[i + 1]
        y_pos = (y_bottom[idx] + y_top[idx]) / 2

        pretty_name = PRETTY_NAMES_OBJ.get(cat, cat)
        ax.text(
            x_pos,
            y_pos,
            pretty_name,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    for i, cat in enumerate(purely_neg_cats):
        heights = np.abs(y_smooth_split[cat])
        max_height = heights.max()

        if max_height < 15:
            continue

        max_idx = np.argmax(heights)
        x_pos = x_smooth[max_idx]

        if cat in label_x_positions:
            x_pos = label_x_positions[cat]

        log_x_pos = np.log10(x_pos)
        log_x_pos = np.clip(log_x_pos, log_x_min + log_margin, log_x_max - log_margin)
        x_pos = 10**log_x_pos

        idx = np.argmin(np.abs(x_smooth - x_pos))
        y_top = y_neg_stacks[i]
        y_bottom = y_neg_stacks[i + 1]
        y_pos = (y_bottom[idx] + y_top[idx]) / 2

        pretty_name = PRETTY_NAMES_OBJ.get(cat, cat)
        ax.text(
            x_pos,
            y_pos,
            pretty_name,
            ha="center",
            va="center",
            fontsize=label_fontsize,
            fontweight="bold",
            color="black",
            bbox=bbox_style,
        )

    if health_value is not None or ghg_price is not None:
        note_lines = ["Fixed in this plot:"]
        if health_value is not None:
            note_lines.append(f"  Health: ${health_value:,.0f}/YLL")
        if ghg_price is not None:
            note_lines.append(f"  GHG: ${ghg_price:,.0f}/tCO2eq")
        note_text = "\n".join(note_lines)
        note_bbox = {
            "boxstyle": "round,pad=0.3",
            "facecolor": "lightyellow",
            "edgecolor": "none",
            "alpha": 0.9,
        }
        ax.text(
            0.98,
            0.97,
            note_text,
            transform=ax.transAxes,
            fontsize=FONTSIZE_TICK_LABEL,
            va="top",
            ha="right",
            bbox=note_bbox,
        )

    ax.set_xscale("log")
    ax.set_xlabel(xlabel, fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("Cost [billion USD]", fontsize=FONTSIZE_AXIS_LABEL)

    ax.text(
        -0.10,
        1.05,
        panel_label,
        transform=ax.transAxes,
        fontsize=FONTSIZE_PANEL_LABEL,
        fontweight="bold",
        va="top",
        ha="left",
    )

    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_ticklabels)
    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)
    ax.set_xlim(x_min, x_max)

    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3, which="both")
    ax.set_axisbelow(True)


# -----------------------------------------------------------------------------
# Grid data loading and heatmap plotting
# -----------------------------------------------------------------------------


def load_grid_data_from_statistics(
    scenarios: list[tuple[float, float, str, Path]],
    project_root: Path,
    config_name: str,
) -> pd.DataFrame:
    """Load grid data from pre-computed analysis CSVs.

    Args:
        scenarios: List of (ghg_price, yll_value, scenario_name, network_path) tuples
        project_root: Path to project root directory
        config_name: Name of the config (e.g., 'ghg_yll_grid')

    Returns:
        DataFrame with MultiIndex (ghg_price, yll_value) and columns for
        cost components (billion USD), health_myll, and ghg_mtco2eq.
    """
    results_dir = project_root / "results" / config_name

    grid_data = {}
    for ghg_price, yll_value, scenario_name, _ in scenarios:
        analysis_dir = results_dir / "analysis" / f"scen-{scenario_name}"

        obj_df = pd.read_csv(analysis_dir / "objective_breakdown.csv")
        net_df = _load_net_emissions_csv(analysis_dir / "net_emissions.csv")
        health_df = pd.read_csv(analysis_dir / "health_totals.csv")

        ghg_mtco2eq = net_df["mtco2eq"].sum()

        grid_data[(ghg_price, yll_value)] = {
            "crop_production": obj_df["crop_production"].iloc[0],
            "trade": obj_df["trade"].iloc[0],
            "consumer_values": obj_df.get("consumer_values", pd.Series([0.0])).iloc[0],
            "fertilizer": obj_df["fertilizer"].iloc[0],
            "ghg_mtco2eq": ghg_mtco2eq,
            "health_myll": health_df["yll_myll"].sum(),
            "total_objective": obj_df.iloc[0].sum(),
        }

    df = pd.DataFrame(grid_data).T
    df.index = pd.MultiIndex.from_tuples(df.index, names=["ghg_price", "yll_value"])
    return df.sort_index()


def pivot_grid_data(
    df: pd.DataFrame,
    value_col: str,
) -> pd.DataFrame:
    """Pivot grid data into 2D matrix for heatmap plotting.

    Args:
        df: DataFrame with MultiIndex (ghg_price, yll_value)
        value_col: Column to pivot

    Returns:
        DataFrame with ghg_price as index (rows) and yll_value as columns
    """
    df_reset = df.reset_index()
    return df_reset.pivot(index="ghg_price", columns="yll_value", values=value_col)


def plot_heatmap(
    data: pd.DataFrame,
    ax: plt.Axes,
    title: str,
    cbar_label: str,
    cmap: str = "viridis",
    panel_label: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    vcenter: float | None = None,
    log_scale_cbar: bool = False,
    baseline_value: float | None = None,
    baseline_label: str = "Baseline",
    cbar_orientation: str = "vertical",
):
    """Plot a heatmap with logarithmic axes.

    Args:
        data: 2D DataFrame with ghg_price as index and yll_value as columns
        ax: Matplotlib axes to plot on
        title: Plot title
        cbar_label: Colorbar label
        cmap: Colormap name
        panel_label: Panel label (e.g., 'a', 'b')
        vmin: Minimum value for colorbar
        vmax: Maximum value for colorbar
        vcenter: Center value for diverging colormap normalization
        log_scale_cbar: Whether to use log scale for colorbar
        baseline_value: Value to mark on colorbar (e.g., from (0,0) scenario)
        baseline_label: Label for the baseline marker
        cbar_orientation: Colorbar orientation ('vertical' or 'horizontal')
    """
    from matplotlib.colors import LogNorm, TwoSlopeNorm

    ghg_values = data.index.values.astype(float)
    yll_values = data.columns.values.astype(float)

    # Create cell edges for pcolormesh in log space
    # For n data points, we need n+1 edges
    def log_edges(values):
        """Create cell edges in log space for given center values."""
        log_vals = np.log10(values)
        edges = np.zeros(len(values) + 1)
        # Interior edges are midpoints in log space
        for i in range(1, len(values)):
            edges[i] = 10 ** ((log_vals[i - 1] + log_vals[i]) / 2)
        # Exterior edges extend by half a cell width in log space
        if len(values) > 1:
            half_width_left = (log_vals[1] - log_vals[0]) / 2
            half_width_right = (log_vals[-1] - log_vals[-2]) / 2
        else:
            half_width_left = half_width_right = 0.5
        edges[0] = 10 ** (log_vals[0] - half_width_left)
        edges[-1] = 10 ** (log_vals[-1] + half_width_right)
        return edges

    yll_edges = log_edges(yll_values)
    ghg_edges = log_edges(ghg_values)

    if log_scale_cbar and vmin is not None and vmin > 0:
        norm = LogNorm(vmin=vmin, vmax=vmax)
        im = ax.pcolormesh(
            yll_edges,
            ghg_edges,
            data.values,
            cmap=cmap,
            norm=norm,
        )
    elif (
        vcenter is not None
        and (vmin if vmin is not None else np.nanmin(data.values)) < vcenter
        and (vmax if vmax is not None else np.nanmax(data.values)) > vcenter
    ):
        norm = TwoSlopeNorm(
            vmin=vmin if vmin is not None else np.nanmin(data.values),
            vcenter=vcenter,
            vmax=vmax if vmax is not None else np.nanmax(data.values),
        )
        im = ax.pcolormesh(
            yll_edges,
            ghg_edges,
            data.values,
            cmap=cmap,
            norm=norm,
        )
    else:
        im = ax.pcolormesh(
            yll_edges,
            ghg_edges,
            data.values,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
        )

    ax.set_xscale("log")
    ax.set_yscale("log")

    # Set axis limits to match the cell edges
    ax.set_xlim(yll_edges[0], yll_edges[-1])
    ax.set_ylim(ghg_edges[0], ghg_edges[-1])

    # Set ticks at nice log positions plus extremes, no scientific notation
    from matplotlib.ticker import FuncFormatter, LogLocator

    def _format_tick(x, pos):
        """Format tick label without scientific notation."""
        if x >= 1000:
            if x == int(x):
                return f"{int(x):,}".replace(",", " ")
            return f"{x:,.0f}".replace(",", " ")
        elif x >= 1:
            return f"{int(x)}" if x == int(x) else f"{x:.1f}"
        else:
            return f"{x:.1f}"

    def _get_nice_ticks(vmin, vmax, sparse=False):
        """Get nice tick positions including extremes."""
        import math

        ticks = []
        decade_start = math.floor(math.log10(vmin))
        decade_end = math.ceil(math.log10(vmax))
        # Use sparser ticks (1, 5) for x-axis, denser (1, 2, 5) for y-axis
        mults = [1, 5] if sparse else [1, 2, 5]
        for decade in range(decade_start, decade_end + 1):
            base = 10**decade
            for mult in mults:
                val = base * mult
                if vmin <= val <= vmax:
                    ticks.append(val)
        # Add extremes if not close to existing ticks
        for extreme in [vmin, vmax]:
            if not any(abs(t - extreme) / extreme < 0.1 for t in ticks):
                ticks.append(extreme)
        return sorted(ticks)

    def _get_powers_of_10(vmin, vmax):
        """Get powers of 10 within range for major ticks."""
        import math

        powers = []
        start = math.floor(math.log10(vmin))
        end = math.ceil(math.log10(vmax))
        for exp in range(start, end + 1):
            val = 10**exp
            if vmin <= val <= vmax:
                powers.append(val)
        return powers

    # Get nice label positions and powers of 10
    x_nice = _get_nice_ticks(yll_values.min(), yll_values.max(), sparse=True)
    y_nice = _get_nice_ticks(ghg_values.min(), ghg_values.max(), sparse=False)
    x_powers = _get_powers_of_10(yll_values.min(), yll_values.max())
    y_powers = _get_powers_of_10(ghg_values.min(), ghg_values.max())

    # Major ticks at powers of 10 (longer marks)
    ax.set_xticks(x_powers)
    ax.set_yticks(y_powers)

    # Minor ticks at all integer multiples (1-9 within each decade)
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))

    # Custom formatters that show labels at nice positions
    def _make_selective_formatter(nice_ticks):
        def _formatter(x, pos):
            for t in nice_ticks:
                if abs(x - t) / t < 0.01:
                    return _format_tick(x, pos)
            return ""

        return _formatter

    ax.xaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(y_nice)))
    ax.xaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(y_nice)))

    # Rotate x-axis labels to avoid overlap and anchor at their tip
    # Use which="both" to apply to major and minor ticks
    ax.tick_params(
        axis="x", which="both", rotation=45, pad=2, labelsize=FONTSIZE_TICK_LABEL
    )
    ax.tick_params(axis="y", which="both", pad=2, labelsize=FONTSIZE_TICK_LABEL)
    plt.setp(ax.get_xticklabels(), ha="right")
    plt.setp(ax.xaxis.get_minorticklabels(), ha="right")

    ax.set_xlabel("YLL value [USD/YLL]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("GHG price [USD/tCO2eq]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_title(title, fontsize=FONTSIZE_TITLE)

    cbar = plt.colorbar(im, ax=ax, orientation=cbar_orientation, pad=0.25)
    cbar.set_label(cbar_label, fontsize=FONTSIZE_CBAR_LABEL)
    cbar.ax.tick_params(labelsize=FONTSIZE_TICK_LABEL)

    # Add baseline marker on colorbar
    if baseline_value is not None:
        cbar_vmin = vmin if vmin is not None else data.values.min()
        cbar_vmax = vmax if vmax is not None else data.values.max()
        # Only show marker if within colorbar range
        if cbar_vmin <= baseline_value <= cbar_vmax:
            if cbar_orientation == "horizontal":
                cbar.ax.axvline(x=baseline_value, color="black", linewidth=1.5)
            else:
                cbar.ax.axhline(y=baseline_value, color="black", linewidth=1.5)
                cbar.ax.plot(
                    1.0,
                    baseline_value,
                    marker="<",
                    color="black",
                    markersize=6,
                    transform=cbar.ax.get_yaxis_transform(),
                    clip_on=False,
                )

    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)

    # Make spines faint grey on plot and colorbar
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")
    for spine in cbar.ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")

    if panel_label is not None:
        ax.text(
            -0.05,
            1.12,
            panel_label,
            transform=ax.transAxes,
            fontsize=FONTSIZE_PANEL_LABEL,
            fontweight="bold",
            va="top",
            ha="left",
        )


def _nice_contour_levels(vmin: float, vmax: float, n_levels: int = 8) -> np.ndarray:
    """Generate nice round contour levels between vmin and vmax.

    Prefers values like 1, 2, 5, 10, 20, 50, 100, 200, 500, etc.
    """
    data_range = vmax - vmin
    if data_range <= 0:
        return np.array([vmin])

    # Estimate rough step size
    rough_step = data_range / n_levels

    # Find the order of magnitude
    magnitude = 10 ** np.floor(np.log10(rough_step))

    # Nice step multipliers
    nice_multipliers = [1, 2, 5, 10, 20, 50]

    # Find the best nice step
    best_step = None
    best_n = 0
    for mult in nice_multipliers:
        step = mult * magnitude
        n = int(np.floor((vmax - vmin) / step)) + 1
        if 4 <= n <= n_levels + 4 and (
            best_step is None or abs(n - n_levels) < abs(best_n - n_levels)
        ):
            best_step = step
            best_n = n

    if best_step is None:
        best_step = rough_step

    # Generate levels starting from a nice number
    start = np.ceil(vmin / best_step) * best_step
    levels = []
    level = start
    while level <= vmax:
        levels.append(level)
        level += best_step

    # Ensure we have at least the min and max in range
    if len(levels) == 0:
        levels = [vmin, vmax]

    return np.array(levels)


def plot_contour(
    data: pd.DataFrame,
    ax: plt.Axes,
    title: str,
    cbar_label: str,
    cmap: str = "viridis",
    panel_label: str | None = None,
    n_levels: int = 8,
    n_interp: int = 200,
    baseline_value: float | None = None,
    label_fmt: str = "%.0f",
    vmin: float | None = None,
    vmax: float | None = None,
    vcenter: float | None = None,
    contour_levels: np.ndarray | None = None,
    cbar_orientation: str = "vertical",
    sigma: float = 0,
):
    """Plot smoothed contour lines with logarithmic axes.

    Args:
        data: 2D DataFrame with ghg_price as index and yll_value as columns
        ax: Matplotlib axes to plot on
        title: Plot title
        cbar_label: Colorbar label
        cmap: Colormap name
        panel_label: Panel label (e.g., 'a', 'b')
        n_levels: Approximate number of contour levels (used if contour_levels not provided)
        n_interp: Number of interpolation points per axis
        baseline_value: Value to highlight with a dashed contour line
        label_fmt: Format string for contour labels
        vmin: Minimum value for color scale
        vmax: Maximum value for color scale
        vcenter: Center value for diverging colormap normalization
        contour_levels: Explicit contour levels (overrides n_levels)
        cbar_orientation: Colorbar orientation ('vertical' or 'horizontal')
        sigma: Gaussian smoothing sigma (0 = no smoothing, applied on interpolated grid)
    """
    from matplotlib.colors import TwoSlopeNorm
    from scipy.interpolate import RegularGridInterpolator
    from scipy.ndimage import gaussian_filter

    ghg_values = data.index.values.astype(float)
    yll_values = data.columns.values.astype(float)

    # Create fine grid in log space for smooth contours
    log_ghg = np.log10(ghg_values)
    log_yll = np.log10(yll_values)

    log_ghg_fine = np.linspace(log_ghg.min(), log_ghg.max(), n_interp)
    log_yll_fine = np.linspace(log_yll.min(), log_yll.max(), n_interp)

    # Interpolate data onto fine grid using cubic interpolation
    interp = RegularGridInterpolator(
        (log_ghg, log_yll),
        data.values,
        method="cubic",
        bounds_error=False,
        fill_value=None,
    )

    log_yll_mesh, log_ghg_mesh = np.meshgrid(log_yll_fine, log_ghg_fine)
    points = np.column_stack([log_ghg_mesh.ravel(), log_yll_mesh.ravel()])
    z_fine = interp(points).reshape(log_ghg_mesh.shape)

    # Apply Gaussian smoothing if sigma > 0
    if sigma > 0:
        z_fine = gaussian_filter(z_fine, sigma=sigma)

    # Convert back to linear scale for plotting
    yll_fine = 10**log_yll_fine
    ghg_fine = 10**log_ghg_fine

    # Color scale limits
    color_vmin = vmin if vmin is not None else data.values.min()
    color_vmax = vmax if vmax is not None else data.values.max()

    # Plot continuous colors using pcolormesh
    if vcenter is not None and color_vmin < vcenter and color_vmax > vcenter:
        norm = TwoSlopeNorm(vmin=color_vmin, vcenter=vcenter, vmax=color_vmax)
        im = ax.pcolormesh(
            yll_fine,
            ghg_fine,
            z_fine,
            cmap=cmap,
            norm=norm,
            shading="gouraud",
        )
    else:
        im = ax.pcolormesh(
            yll_fine,
            ghg_fine,
            z_fine,
            cmap=cmap,
            vmin=color_vmin,
            vmax=color_vmax,
            shading="gouraud",
        )

    # Compute nice contour levels
    if contour_levels is None:
        if vcenter is not None and color_vmin < vcenter < color_vmax:
            # Generate separate levels for each side of vcenter so both sides
            # get evenly spaced ticks (avoids the dominant side swallowing all levels)
            n_neg = max(
                2, int(n_levels * (vcenter - color_vmin) / (color_vmax - color_vmin))
            )
            n_pos = max(2, n_levels - n_neg)
            levels_neg = _nice_contour_levels(color_vmin, vcenter, n_neg)
            levels_pos = _nice_contour_levels(vcenter, color_vmax, n_pos)
            # Remove duplicate vcenter if present in both
            contour_levels = np.unique(np.concatenate([levels_neg, levels_pos]))
        else:
            contour_levels = _nice_contour_levels(color_vmin, color_vmax, n_levels)

    # Add contour lines at nice levels
    cs = ax.contour(
        yll_fine,
        ghg_fine,
        z_fine,
        levels=contour_levels,
        colors="black",
        linewidths=0.5,
        alpha=0.8,
    )

    # Add contour labels (horizontal to avoid rotation issues with log scales)
    label_bbox = {
        "boxstyle": "round,pad=0.15",
        "facecolor": "white",
        "alpha": 0.7,
        "edgecolor": "none",
    }
    clabels = ax.clabel(
        cs, inline=False, fontsize=FONTSIZE_CONTOUR_LABEL, fmt=label_fmt
    )
    for label in clabels:
        label.set_rotation(0)
        label.set_bbox(label_bbox)
        label.set_clip_on(True)

    # Add baseline contour if provided
    if baseline_value is not None:
        cs_baseline = ax.contour(
            yll_fine,
            ghg_fine,
            z_fine,
            levels=[baseline_value],
            colors="black",
            linewidths=2,
            linestyles="dashed",
        )
        clabels_baseline = ax.clabel(
            cs_baseline,
            inline=False,
            fontsize=FONTSIZE_CONTOUR_LABEL + 1,
            fmt=label_fmt,
        )
        for label in clabels_baseline:
            label.set_rotation(0)
            label.set_bbox(label_bbox)
            label.set_clip_on(True)

    ax.set_xscale("log")
    ax.set_yscale("log")

    # Set ticks at nice log positions plus extremes, no scientific notation
    from matplotlib.ticker import FuncFormatter, LogLocator

    def _format_tick(x, pos):
        """Format tick label without scientific notation."""
        if x >= 1000:
            if x == int(x):
                return f"{int(x):,}".replace(",", " ")
            return f"{x:,.0f}".replace(",", " ")
        elif x >= 1:
            return f"{int(x)}" if x == int(x) else f"{x:.1f}"
        else:
            return f"{x:.1f}"

    def _get_nice_ticks(vmin, vmax, sparse=False):
        """Get nice tick positions including extremes."""
        import math

        ticks = []
        decade_start = math.floor(math.log10(vmin))
        decade_end = math.ceil(math.log10(vmax))
        # Use sparser ticks (1, 5) for x-axis, denser (1, 2, 5) for y-axis
        mults = [1, 5] if sparse else [1, 2, 5]
        for decade in range(decade_start, decade_end + 1):
            base = 10**decade
            for mult in mults:
                val = base * mult
                if vmin <= val <= vmax:
                    ticks.append(val)
        # Add extremes if not close to existing ticks
        for extreme in [vmin, vmax]:
            if not any(abs(t - extreme) / extreme < 0.1 for t in ticks):
                ticks.append(extreme)
        return sorted(ticks)

    def _get_powers_of_10(vmin, vmax):
        """Get powers of 10 within range for major ticks."""
        import math

        powers = []
        start = math.floor(math.log10(vmin))
        end = math.ceil(math.log10(vmax))
        for exp in range(start, end + 1):
            val = 10**exp
            if vmin <= val <= vmax:
                powers.append(val)
        return powers

    # Get nice label positions and powers of 10
    x_nice = _get_nice_ticks(yll_values.min(), yll_values.max(), sparse=True)
    y_nice = _get_nice_ticks(ghg_values.min(), ghg_values.max(), sparse=False)
    x_powers = _get_powers_of_10(yll_values.min(), yll_values.max())
    y_powers = _get_powers_of_10(ghg_values.min(), ghg_values.max())

    # Major ticks at powers of 10 (longer marks)
    ax.set_xticks(x_powers)
    ax.set_yticks(y_powers)

    # Minor ticks at all integer multiples (1-9 within each decade)
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))
    ax.yaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(1, 10), numticks=100))

    # Custom formatters that show labels at nice positions
    def _make_selective_formatter(nice_ticks):
        def _formatter(x, pos):
            for t in nice_ticks:
                if abs(x - t) / t < 0.01:
                    return _format_tick(x, pos)
            return ""

        return _formatter

    ax.xaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_major_formatter(FuncFormatter(_make_selective_formatter(y_nice)))
    ax.xaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(x_nice)))
    ax.yaxis.set_minor_formatter(FuncFormatter(_make_selective_formatter(y_nice)))

    # Rotate x-axis labels to avoid overlap and anchor at their tip
    # Use which="both" to apply to major and minor ticks
    ax.tick_params(
        axis="x", which="both", rotation=45, pad=2, labelsize=FONTSIZE_TICK_LABEL
    )
    ax.tick_params(axis="y", which="both", pad=2, labelsize=FONTSIZE_TICK_LABEL)
    plt.setp(ax.get_xticklabels(), ha="right")
    plt.setp(ax.xaxis.get_minorticklabels(), ha="right")

    ax.set_xlabel("YLL value [USD/YLL]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_ylabel("GHG price [USD/tCO2eq]", fontsize=FONTSIZE_AXIS_LABEL)
    ax.set_title(title, fontsize=FONTSIZE_TITLE)

    cbar = plt.colorbar(im, ax=ax, orientation=cbar_orientation, pad=0.25)
    cbar.set_label(cbar_label, fontsize=FONTSIZE_CBAR_LABEL)
    cbar.ax.tick_params(labelsize=FONTSIZE_TICK_LABEL)
    cbar_ticks = np.unique(contour_levels.astype(float))
    cbar_ticks = cbar_ticks[
        np.isfinite(cbar_ticks)
        & (cbar_ticks >= color_vmin)
        & (cbar_ticks <= color_vmax)
    ]
    if cbar_ticks.size > 0:
        cbar.set_ticks(cbar_ticks)
        cbar.set_ticklabels([label_fmt % t for t in cbar_ticks])

    # Add baseline marker on colorbar
    if baseline_value is not None:
        cbar_vmin = data.values.min()
        cbar_vmax = data.values.max()
        if cbar_vmin <= baseline_value <= cbar_vmax:
            if cbar_orientation == "horizontal":
                cbar.ax.axvline(
                    x=baseline_value, color="black", linewidth=1.5, linestyle="--"
                )
            else:
                cbar.ax.axhline(
                    y=baseline_value, color="black", linewidth=1.5, linestyle="--"
                )
                cbar.ax.plot(
                    1.0,
                    baseline_value,
                    marker="<",
                    color="black",
                    markersize=6,
                    transform=cbar.ax.get_yaxis_transform(),
                    clip_on=False,
                )

    ax.tick_params(axis="both", labelsize=FONTSIZE_TICK_LABEL)

    # Make spines faint grey on plot and colorbar
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")
    for spine in cbar.ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("0.7")

    if panel_label is not None:
        ax.text(
            -0.05,
            1.12,
            panel_label,
            transform=ax.transAxes,
            fontsize=FONTSIZE_PANEL_LABEL,
            fontweight="bold",
            va="top",
            ha="left",
        )
