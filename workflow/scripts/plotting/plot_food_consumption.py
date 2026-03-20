# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot global food consumption by food group per person per day."""

from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.constants import DAYS_PER_YEAR, GRAMS_PER_MEGATONNE, PJ_TO_KCAL
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors

# Alias for backwards compatibility with modules that import from here
KCAL_PER_PJ = PJ_TO_KCAL


def _load_global_consumption(
    food_group_consumption_path: str, population_path: str
) -> tuple[pd.Series, pd.Series]:
    """Load food group consumption and compute global per-capita values.

    Returns
    -------
    tuple[pd.Series, pd.Series]
        (mass_g_per_person_day, calories_kcal_per_person_day) indexed by food_group
    """
    df = pd.read_parquet(food_group_consumption_path)
    pop_df = pd.read_csv(population_path)

    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    # Sum absolute values across all countries
    global_totals = df.groupby("food_group")[["consumption_mt", "cal_pj"]].sum()

    # Get total population
    total_population = pop_df["population"].sum()

    # Convert to per-capita
    mass_per_capita = (
        global_totals["consumption_mt"]
        * GRAMS_PER_MEGATONNE
        / (total_population * DAYS_PER_YEAR)
    )
    calories_per_capita = (
        global_totals["cal_pj"] * PJ_TO_KCAL / (total_population * DAYS_PER_YEAR)
    )

    return mass_per_capita, calories_per_capita


def _assign_colors(
    groups: list[str], overrides: dict[str, str] | None = None
) -> dict[str, str]:
    return categorical_colors(groups, overrides)


def _plot(
    mass_g_per_person_day: pd.Series,
    calories_kcal_per_person_day: pd.Series,
    output_pdf: Path,
) -> None:
    mass_g_per_person_day = mass_g_per_person_day[mass_g_per_person_day > 0]
    calories_kcal_per_person_day = calories_kcal_per_person_day[
        calories_kcal_per_person_day > 0
    ]

    ordered_groups: list[str] = []
    ordered_groups.extend(
        mass_g_per_person_day.sort_values(ascending=False).index.tolist()
    )
    for group in calories_kcal_per_person_day.sort_values(ascending=False).index:
        if group not in ordered_groups:
            ordered_groups.append(group)

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    if not ordered_groups:
        for ax in axes:
            ax.text(
                0.5, 0.5, "No food group consumption data", ha="center", va="center"
            )
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
        plt.close(fig)
        return

    group_colors = getattr(snakemake.params, "group_colors", {}) or {}
    colors = _assign_colors(ordered_groups, group_colors)

    # Mass subplot
    ax_mass = axes[0]
    bottom = 0.0
    for group in ordered_groups:
        value = float(mass_g_per_person_day.get(group, 0.0))
        if value <= 0.0:
            continue
        ax_mass.bar(0, value, bottom=bottom, color=colors[group], label=group)
        bottom += value

    ax_mass.set_xticks([0])
    ax_mass.set_xticklabels(["Mass"])
    ax_mass.set_ylabel("g/person/day")
    ax_mass.set_title("Global Food Consumption (Mass)")
    ax_mass.grid(axis="y", alpha=0.3)

    # Calories subplot
    ax_cal = axes[1]
    bottom = 0.0
    for group in ordered_groups:
        value = float(calories_kcal_per_person_day.get(group, 0.0))
        if value <= 0.0:
            continue
        ax_cal.bar(0, value, bottom=bottom, color=colors[group])
        bottom += value

    ax_cal.set_xticks([0])
    ax_cal.set_xticklabels(["Calories"])
    ax_cal.set_ylabel("kcal/person/day")
    ax_cal.set_title("Global Food Consumption (Calories)")
    ax_cal.grid(axis="y", alpha=0.3)

    handles, labels = ax_mass.get_legend_handles_labels()
    if handles:
        fig.legend(
            handles[::-1],
            labels[::-1],
            loc="center left",
            bbox_to_anchor=(1.0, 0.5),
        )
        fig.tight_layout(rect=(0, 0, 0.85, 1))
    else:
        fig.tight_layout()

    fig.savefig(output_pdf, bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:  # pragma: no cover - Snakemake injects the variable
        raise RuntimeError("This script must be run from Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    food_group_consumption_path = snakemake.input.food_group_consumption
    population_path = snakemake.input.population
    output_pdf = Path(snakemake.output.pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading food group consumption from %s", food_group_consumption_path)
    mass_per_capita, calories_per_capita = _load_global_consumption(
        food_group_consumption_path, population_path
    )

    logger.info(
        "Found %d food groups with mass data and %d with calorie data",
        mass_per_capita[mass_per_capita > 0].shape[0],
        calories_per_capita[calories_per_capita > 0].shape[0],
    )

    _plot(mass_per_capita, calories_per_capita, output_pdf)

    logger.info("Food consumption plot saved to %s", output_pdf)


if __name__ == "__main__":
    main()
