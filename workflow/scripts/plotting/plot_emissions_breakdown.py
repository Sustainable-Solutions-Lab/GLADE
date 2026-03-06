# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot emissions breakdown by source for CO2, CH4, and N2O.

Reads source-level emissions from the analysis CSV (net_emissions.csv) and
compares against FAOSTAT and GLEAM reference data.
"""

from collections import defaultdict
import logging
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def load_emissions_csv(path: Path) -> dict[str, dict[str, float]]:
    """Load emissions CSV (gas, source, *co2eq) into nested dict."""
    df = pd.read_csv(path, comment="#")
    value_cols = [c for c in df.columns if c.endswith("co2eq")]
    if len(value_cols) != 1:
        raise ValueError(
            f"Expected exactly one *co2eq column in {path}, got {value_cols}"
        )
    value_col = value_cols[0]
    result: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for _, row in df.iterrows():
        gas = str(row["gas"]).upper()
        result[gas][row["source"]] += float(row[value_col])
    return {gas: dict(srcs) for gas, srcs in result.items()}


def process_faostat_emissions(
    faostat_df: pd.DataFrame,
    ch4_gwp: float,
    n2o_gwp: float,
) -> dict[str, dict[str, float]]:
    """Process raw FAOSTAT emissions data into a categorized dict in MtCO2eq.

    Parameters
    ----------
    faostat_df : pd.DataFrame
        Raw FAOSTAT GT emissions data.
    ch4_gwp : float
        Global warming potential for CH4 (kg CO2eq / kg CH4)
    n2o_gwp : float
        Global warming potential for N2O (kg CO2eq / kg N2O)

    Returns
    -------
    dict[str, dict[str, float]]
        Nested dict: {gas: {source: amount}}
        All values in MtCO2eq
    """
    faostat_emissions: dict[str, dict[str, float]] = {
        "CO2": defaultdict(float),
        "CH4": defaultdict(float),
        "N2O": defaultdict(float),
    }

    item_to_category = {
        "Crop Residues": "Crop residue incorporation",
        "Rice Cultivation": "Rice cultivation",
        "Burning - Crop residues": "Crop residue burning",
        "Synthetic Fertilizers": "Synthetic fertilizer application",
        "Drained organic soils": "Drained organic soils",
        "Drained organic soils (CO2)": "Drained organic soils",
        "Drained organic soils (N2O)": "Drained organic soils",
        "Enteric Fermentation": "Enteric fermentation",
        "Manure Management": "Manure: managed systems",
        "Manure applied to Soils": "Manure: managed systems",
        "Manure left on Pasture": "Manure: pasture deposition",
        "Net Forest conversion": "Land Use Change",
    }

    element_to_gas = {
        "Emissions (CH4)": ("CH4", ch4_gwp),
        "Emissions (N2O)": ("N2O", n2o_gwp),
        "Emissions (CO2)": ("CO2", 1.0),
    }

    for _, row in faostat_df.iterrows():
        item = row["item"]
        element = row["element"]
        value_kt = row["value_kt"]

        category = item_to_category.get(item)
        if category is None:
            if item.startswith("Drained organic soils"):
                category = "Drained organic soils"
            else:
                logger.debug("Skipping unknown FAOSTAT item: %s", item)
                continue

        gas_info = element_to_gas.get(element)
        if gas_info is None:
            logger.debug("Skipping unknown FAOSTAT element: %s", element)
            continue

        gas_name, gwp_factor = gas_info
        value_mtco2eq = value_kt * 1e-3 * gwp_factor
        faostat_emissions[gas_name][category] += value_mtco2eq

    return faostat_emissions


def plot_emissions_breakdown(
    emissions: dict[str, dict[str, float]],
    faostat_emissions: dict[str, dict[str, float]],
    gleam_emissions: dict[str, dict[str, float]] | None,
    output_path: Path,
) -> None:
    """Create side-by-side stacked bar plots for each gas in CO2eq units.

    Parameters
    ----------
    emissions : dict[str, dict[str, float]]
        Modeled emissions data by gas and source (all in MtCO2eq)
    faostat_emissions : dict[str, dict[str, float]]
        FAOSTAT actual emissions data by gas and source (all in MtCO2eq)
    gleam_emissions : dict[str, dict[str, float]] | None
        GLEAM livestock emissions data (optional)
    output_path : Path
        Path to save the PDF plot
    """
    fig, axes = plt.subplots(1, 3, figsize=(14, 7), sharey=True)

    fig.suptitle(
        "Global Emissions Breakdown by Source: Modeled vs. FAOSTAT",
        fontsize=16,
        fontweight="bold",
        y=1.02,
    )

    gas_cmaps = {
        "CO2": "Greys",
        "CH4": "Greens",
        "N2O": "Oranges",
    }

    for idx, gas in enumerate(["CO2", "CH4", "N2O"]):
        ax = axes[idx]

        modeled_data = emissions.get(gas, {})
        actual_data = faostat_emissions.get(gas, {})
        gleam_data = gleam_emissions.get(gas, {}) if gleam_emissions else {}

        all_sources = sorted(
            set(modeled_data.keys()) | set(actual_data.keys()) | set(gleam_data.keys())
        )
        n_cats = len(all_sources)

        if not modeled_data and not actual_data:
            ax.text(0.5, 0.5, f"No {gas} emissions", ha="center", va="center")
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis("off")
            continue

        cmap_name = gas_cmaps.get(gas, "Blues")
        cmap = matplotlib.colormaps[cmap_name]

        if n_cats <= 1:
            colors_for_categories = [cmap(0.6)]
        else:
            colors_for_categories = [
                cmap(0.3 + 0.6 * i / (n_cats - 1)) for i in range(n_cats)
            ]
        colors_for_categories.reverse()
        category_colors = dict(zip(all_sources, colors_for_categories))

        bar_width = 0.5
        x_modeled = 0.0
        x_actual = 1.0
        x_gleam = 2.0

        def stacked_bar(
            x_pos: float,
            data: dict[str, float],
            sources: list[str],
            colors: dict[str, str],
            axis: plt.Axes,
            width: float,
        ) -> None:
            bottom_pos = 0.0
            bottom_neg = 0.0
            for source in sources:
                value = data.get(source, 0.0)
                color = colors.get(source, "#d9d9d9")
                if value > 0:
                    axis.bar(
                        x_pos,
                        value,
                        bottom=bottom_pos,
                        width=width,
                        color=color,
                        edgecolor="black",
                        linewidth=0.5,
                        label=source
                        if source not in axis.get_legend_handles_labels()[1]
                        else "",
                    )
                    bottom_pos += value
                elif value < 0:
                    axis.bar(
                        x_pos,
                        value,
                        bottom=bottom_neg,
                        width=width,
                        color=color,
                        edgecolor="black",
                        linewidth=0.5,
                        label=source
                        if source not in axis.get_legend_handles_labels()[1]
                        else "",
                    )
                    bottom_neg += value

        stacked_bar(
            x_modeled, modeled_data, all_sources, category_colors, ax, bar_width
        )
        stacked_bar(x_actual, actual_data, all_sources, category_colors, ax, bar_width)
        if gleam_emissions is not None:
            stacked_bar(
                x_gleam, gleam_data, all_sources, category_colors, ax, bar_width
            )

        ax.set_title(gas, fontsize=14, fontweight="bold")
        if idx == 0:
            ax.set_ylabel("Emissions (MtCO\u2082eq)", fontsize=12)
        xticks = [x_modeled, x_actual]
        xticklabels = ["Modeled", "FAOSTAT"]
        if gleam_emissions is not None:
            xticks.append(x_gleam)
            xticklabels.append("GLEAM\n(livestock)")
        ax.set_xticks(xticks)
        ax.set_xticklabels(xticklabels, fontsize=10)
        xmin = x_modeled - bar_width * 0.75
        xmax = (
            x_gleam + bar_width * 0.75
            if gleam_emissions is not None
            else x_actual + bar_width * 0.75
        )
        ax.set_xlim(xmin, xmax)

        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.axhline(y=0, color="black", linewidth=0.8)

        handles, labels = ax.get_legend_handles_labels()
        unique_labels = []
        unique_handles = []
        for handle, label in zip(handles, labels):
            if label and label not in unique_labels:
                unique_labels.append(label)
                unique_handles.append(handle)

        sorted_unique_labels = [
            label for label in all_sources if label in unique_labels
        ]
        sorted_unique_handles = [
            unique_handles[unique_labels.index(label)] for label in sorted_unique_labels
        ]

        ax.legend(
            reversed(sorted_unique_handles),
            reversed(sorted_unique_labels),
            loc="upper center",
            bbox_to_anchor=(0.5, -0.1),
            ncol=1,
            frameon=False,
            fontsize=8,
            title="Sources",
            title_fontsize=9,
        )

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.35, wspace=0.3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote emissions breakdown plot to %s", output_path)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    ch4_gwp = float(snakemake.params.ch4_gwp)
    n2o_gwp = float(snakemake.params.n2o_gwp)

    # Load modeled emissions from analysis CSV
    logger.info("Loading modeled emissions from %s", snakemake.input.net_emissions)
    emissions = load_emissions_csv(Path(snakemake.input.net_emissions))

    logger.info("Loading and processing FAOSTAT emissions data")
    faostat_emissions_df = pd.read_csv(snakemake.input.faostat_emissions)
    faostat_emissions_processed = process_faostat_emissions(
        faostat_emissions_df, ch4_gwp, n2o_gwp
    )

    gleam_emissions = load_emissions_csv(Path(snakemake.input.gleam_emissions))

    # Log summary
    for gas, sources in emissions.items():
        total = sum(sources.values())
        logger.info("%s total: %.2f MtCO2eq", gas, total)
        for source, amount in sorted(
            sources.items(), key=lambda x: abs(x[1]), reverse=True
        ):
            logger.info("  %s: %.2f MtCO2eq", source, amount)

    pdf_path = Path(snakemake.output.pdf)

    plot_emissions_breakdown(
        emissions,
        faostat_emissions_processed,
        gleam_emissions,
        pdf_path,
    )
