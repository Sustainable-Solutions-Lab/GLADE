# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot emissions breakdown by source for CO2, CH4, and N2O."""

from collections import defaultdict
import logging
from pathlib import Path

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib

matplotlib.use("pdf")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def categorize_emission_carrier(carrier: str, bus_carrier: str) -> str:
    """Categorize an emission source by its carrier and gas type.

    Parameters
    ----------
    carrier : str
        Link carrier name
    bus_carrier : str
        The emission bus being fed ("co2", "ch4", "n2o")

    Returns
    -------
    str
        Category name for plotting
    """
    # Map specific carriers to categories based on documentation
    # Note: use link carriers, not bus/store carriers
    carrier_map = {
        "residue_incorporation": "Crop residue incorporation",
        "spare_land": "Carbon sequestration",  # Link carrier (not "spared_land" which is bus/store)
        "fertilizer_distribution": "Synthetic fertilizer application",
        "land_conversion": "Land Use Change",  # Link carrier for land expansion
    }

    if carrier in carrier_map:
        return carrier_map[carrier]

    # Pattern-based categorization
    if carrier == "crop_production":
        if bus_carrier == "ch4":
            return "Rice cultivation"
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Crop production"
    elif carrier == "crop_production_multi":
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Multi-cropping"
    elif carrier == "animal_production":
        # Animal production carrier
        if bus_carrier == "n2o":
            return "Manure management & application"
        elif bus_carrier == "ch4":
            # Combined enteric + manure CH4
            return "Enteric fermentation & Manure management"
        return "Livestock production"
    elif carrier == "grassland_production":
        if bus_carrier == "co2":
            return "Land Use Change"
        return "Grassland"
    elif carrier == "food_processing":
        return "Food processing"
    elif carrier.startswith("trade_"):
        return "Trade"
    else:
        # Return carrier name for unknown types
        return f"Other ({carrier})"


def extract_emissions_by_source(
    n: pypsa.Network,
    ch4_gwp: float,
    n2o_gwp: float,
) -> dict[str, dict[str, float]]:
    """Extract emissions by gas type and source category in CO2eq units.

    Uses n.statistics.energy_balance() to efficiently extract emission flows.
    Excludes conversion links (co2, ch4, n2o) that move emissions to the GHG bus.

    Parameters
    ----------
    n : pypsa.Network
        Solved network
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
    # Initialize nested dict for emissions by gas and source
    emissions: dict[str, dict[str, float]] = {
        "CO2": defaultdict(float),
        "CH4": defaultdict(float),
        "N2O": defaultdict(float),
    }

    # GWP factors for each gas
    gwp_factors = {
        "co2": ("CO2", 1.0),
        "ch4": ("CH4", ch4_gwp),
        "n2o": ("N2O", n2o_gwp),
    }

    # Carriers representing conversion links to be excluded (sinks)
    # - co2, ch4, n2o: links that feed into individual gas buses
    # - emission_aggregation: links that move emissions from gas buses to GHG bus
    conversion_carriers = {"co2", "ch4", "n2o", "emission_aggregation"}

    # Get energy balance with grouping by bus_carrier and carrier
    # This gives us flows into each bus, grouped by component carrier
    try:
        balance = n.statistics.energy_balance(groupby=["bus_carrier", "carrier"])
    except Exception as e:
        logger.error("Failed to compute energy balance: %s", e)
        return emissions

    # The balance is a multi-indexed Series with (component, bus_carrier, carrier)
    # We want to extract flows into co2, ch4, and n2o buses
    for (_component, bus_carrier, carrier), value in balance.items():
        # Skip if not an emission bus
        if bus_carrier not in gwp_factors:
            continue

        # Skip conversion links (which appear as negative flows/sinks)
        if carrier in conversion_carriers:
            continue

        # Skip zero or negligible flows
        if abs(value) < 1e-9:
            continue

        gas_name, gwp_factor = gwp_factors[bus_carrier]

        # Convert to CO2eq; CH4 and N2O flows are in tonnes
        value_mt = value * 1e-6 if gas_name in ["CH4", "N2O"] else value

        emission_co2eq = value_mt * gwp_factor

        # Categorize by carrier, passing the bus_carrier (gas type) context
        category = categorize_emission_carrier(carrier, bus_carrier)

        # Add to the appropriate category
        emissions[gas_name][category] += emission_co2eq
        logger.debug(
            "Added %.3f MtCO2eq of %s from %s (carrier: %s)",
            emission_co2eq,
            gas_name,
            category,
            carrier,
        )

    # --- Split manure N2O into pasture vs managed using link-level shares ------
    # Each animal production link has a pasture_n2o_share attribute that gives
    # the fraction of its N2O coming from pasture deposition vs managed systems.
    # This is based on MMS (Manure Management System) distributions from GLEAM.
    if "Manure management & application" in emissions.get("N2O", {}):
        links_df = n.links.static
        produce_mask = links_df.carrier == "animal_production"
        pasture_share = (
            links_df.loc[produce_mask, "pasture_n2o_share"].fillna(0.0).astype(float)
        )

        p4 = n.links.dynamic["p4"].loc[:, produce_mask]
        weights = n.snapshot_weightings["objective"]
        pasture_t_n2o = -(
            p4.multiply(pasture_share, axis=1).multiply(weights, axis=0).sum().sum()
        )
        pasture_mtco2eq = pasture_t_n2o * n2o_gwp * 1e-6

        total_mtco2eq = emissions["N2O"].get("Manure management & application", 0.0)
        managed_mtco2eq = max(total_mtco2eq - pasture_mtco2eq, 0.0)

        emissions["N2O"].pop("Manure management & application", None)
        emissions["N2O"]["Manure: pasture deposition"] = pasture_mtco2eq
        emissions["N2O"]["Manure: managed systems"] = managed_mtco2eq

    # --- Split CH4 into enteric vs manure using link-level shares -------------
    if "Enteric fermentation & Manure management" in emissions.get("CH4", {}):
        links_df = n.links.static
        produce_mask = links_df.carrier == "animal_production"
        manure_share = (
            links_df.loc[produce_mask, "manure_ch4_share"].fillna(0.0).astype(float)
        )

        p2 = n.links.dynamic["p2"].loc[:, produce_mask]
        weights = n.snapshot_weightings["objective"]
        manure_t_ch4 = -(
            p2.multiply(manure_share, axis=1).multiply(weights, axis=0).sum().sum()
        )
        manure_mtco2eq = manure_t_ch4 * ch4_gwp * 1e-6

        total_mtco2eq = emissions["CH4"].get(
            "Enteric fermentation & Manure management", 0.0
        )
        enteric_mtco2eq = max(total_mtco2eq - manure_mtco2eq, 0.0)

        emissions["CH4"].pop("Enteric fermentation & Manure management", None)
        emissions["CH4"]["Enteric fermentation"] = enteric_mtco2eq
        emissions["CH4"]["Manure: managed systems"] = manure_mtco2eq

    return emissions


def extract_emissions_by_region(
    n: pypsa.Network,
    ch4_gwp: float,
    n2o_gwp: float,
    regions_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Extract emissions and land area by region for intensity calculation.

    Uses vectorized pandas operations for efficiency. For country-level links
    (no region), emissions are distributed to regions proportionally by land area.

    Parameters
    ----------
    n : pypsa.Network
        Solved network
    ch4_gwp : float
        Global warming potential for CH4 (kg CO2eq / kg CH4)
    n2o_gwp : float
        Global warming potential for N2O (kg CO2eq / kg N2O)
    regions_gdf : gpd.GeoDataFrame
        Regions GeoDataFrame with 'region' and 'country' columns

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: CO2, CH4, N2O (MtCO2eq), land_area (Mha)
        Index is region name.
    """
    links = n.links.static.copy()
    buses = n.buses.static
    weights = n.snapshot_weightings["objective"]

    # Compute weighted flow for all links at once
    p0 = n.links.dynamic["p0"]
    links["flow"] = (p0 * weights.values[:, None]).sum(axis=0)

    # Identify region vs country level links
    links["has_region"] = (links["region"].notna()) & (links["region"] != "")

    # GWP config: bus_carrier -> (output_col, gwp, unit_factor)
    gas_config = {
        "co2": ("CO2", 1.0, 1.0),
        "ch4": ("CH4", ch4_gwp, 1e-6),
        "n2o": ("N2O", n2o_gwp, 1e-6),
    }

    # Carriers to exclude
    conversion_carriers = {"co2", "ch4", "n2o", "emission_aggregation"}
    land_use_carriers = {
        "crop_production",
        "crop_production_multi",
        "grassland_production",
    }

    # --- Land area by region (vectorized) ---
    land_mask = links["carrier"].isin(land_use_carriers) & links["has_region"]
    land_area_by_region = (
        links.loc[land_mask & (links["flow"] > 0)]
        .groupby("region")["flow"]
        .sum()
        .rename("land_area")
    )

    # --- Emissions by region and country (vectorized) ---
    # Filter out conversion carriers
    emit_links = links[~links["carrier"].isin(conversion_carriers)].copy()

    # Build bus carrier lookup
    bus_carrier_map = buses["carrier"].to_dict()

    # Process each emission port (bus2, bus3, bus4 typically have emissions)
    region_emissions = []
    country_emissions = []

    for port_idx in range(2, 5):
        bus_col = f"bus{port_idx}"
        eff_col = f"efficiency{port_idx}"

        if bus_col not in emit_links.columns or eff_col not in emit_links.columns:
            continue

        # Get bus carrier for each link
        port_bus_carrier = emit_links[bus_col].map(bus_carrier_map)

        # Filter to emission buses only
        is_emission = port_bus_carrier.isin(gas_config.keys())
        port_links = emit_links[is_emission].copy()

        if port_links.empty:
            continue

        port_bus_carrier = port_bus_carrier[is_emission]
        port_links["bus_carrier"] = port_bus_carrier

        # Get efficiency and compute emissions
        eff = port_links[eff_col].fillna(0.0)
        port_links["emission"] = port_links["flow"] * eff

        # Apply GWP and unit conversion
        for bus_carrier, (col_name, gwp, unit_factor) in gas_config.items():
            mask = port_links["bus_carrier"] == bus_carrier
            port_links.loc[mask, "emission"] *= gwp * unit_factor
            port_links.loc[mask, "gas"] = col_name

        # Split into region-level and country-level
        has_region = port_links["has_region"]

        if has_region.any():
            region_df = port_links.loc[has_region, ["region", "gas", "emission"]]
            region_emissions.append(region_df)

        if (~has_region).any():
            country_df = port_links.loc[~has_region, ["country", "gas", "emission"]]
            country_emissions.append(country_df)

    # --- Aggregate region-level emissions ---
    if region_emissions:
        all_region = pd.concat(region_emissions, ignore_index=True)
        region_pivot = all_region.pivot_table(
            index="region",
            columns="gas",
            values="emission",
            aggfunc="sum",
            fill_value=0.0,
        )
    else:
        region_pivot = pd.DataFrame(columns=["CO2", "CH4", "N2O"])

    # --- Aggregate country-level emissions ---
    if country_emissions:
        all_country = pd.concat(country_emissions, ignore_index=True)
        country_pivot = all_country.pivot_table(
            index="country",
            columns="gas",
            values="emission",
            aggfunc="sum",
            fill_value=0.0,
        )
    else:
        country_pivot = pd.DataFrame(columns=["CO2", "CH4", "N2O"])

    # --- Build result DataFrame ---
    # Start with land area
    result = land_area_by_region.to_frame()

    # Add region-level emissions
    for col in ["CO2", "CH4", "N2O"]:
        if col in region_pivot.columns:
            result[col] = region_pivot[col]
        else:
            result[col] = 0.0

    result = result.fillna(0.0)

    # --- Distribute country emissions to regions by land area ---
    if not country_pivot.empty:
        # Build region -> country mapping
        region_country = regions_gdf.set_index("region")["country"]

        # Add country to result
        result["country"] = result.index.map(region_country)

        # Compute land area totals by country
        country_land_totals = result.groupby("country")["land_area"].sum()

        # Compute region fraction within each country
        result["country_land_total"] = result["country"].map(country_land_totals)
        result["region_fraction"] = np.where(
            result["country_land_total"] > 0,
            result["land_area"] / result["country_land_total"],
            0.0,
        )

        # Distribute country emissions
        for col in ["CO2", "CH4", "N2O"]:
            if col not in country_pivot.columns:
                continue
            country_col = result["country"].map(country_pivot[col]).fillna(0.0)
            result[col] = result[col] + country_col * result["region_fraction"]

        # Clean up temporary columns
        result = result.drop(
            columns=["country", "country_land_total", "region_fraction"]
        )

    result.index.name = "region"
    return result[["CO2", "CH4", "N2O", "land_area"]]


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

    # Mapping of FAOSTAT items to our categories
    item_to_category = {
        "Crop Residues": "Crop residue incorporation",
        "Rice Cultivation": "Rice cultivation",
        "Burning - Crop residues": "Crop residue burning",
        "Synthetic Fertilizers": "Synthetic fertilizer application",
        "Drained organic soils": "Drained organic soils",
        "Drained organic soils (CO2)": "Drained organic soils",  # Handle variants
        "Drained organic soils (N2O)": "Drained organic soils",
        "Enteric Fermentation": "Enteric fermentation",
        "Manure Management": "Manure: managed systems",
        "Manure applied to Soils": "Manure: managed systems",
        "Manure left on Pasture": "Manure: pasture deposition",
        "Net Forest conversion": "Land Use Change",  # Positive emission
        # "Forestland": "Carbon sequestration",  # Excluded: represents standing forest sink
        # "Food Processing": "Food processing", # Excluded per user request
        # "Food Transport": "Trade", # Excluded per user request
        # "On-farm energy use": "Other (On-farm energy use)", # Excluded per user request
    }

    # Mapping of FAOSTAT elements to gas types
    element_to_gas = {
        "Emissions (CH4)": ("CH4", ch4_gwp),
        "Emissions (N2O)": ("N2O", n2o_gwp),
        "Emissions (CO2)": ("CO2", 1.0),
    }

    for _, row in faostat_df.iterrows():
        item = row["item"]
        element = row["element"]
        value_kt = row["value_kt"]

        # Handle "Drained organic soils" which might appear with suffix in some datasets or processing
        category = item_to_category.get(item)
        if category is None:
            # Try matching prefix for drained organic soils
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

        # Convert kilotonnes to Mt, then to MtCO2eq
        value_mtco2eq = value_kt * 1e-3 * gwp_factor

        faostat_emissions[gas_name][category] += value_mtco2eq

    return faostat_emissions


def load_emissions_csv(path: Path) -> dict[str, dict[str, float]]:
    """Load emissions CSV (gas, source, emissions_mtco2eq) into nested dict."""

    df = pd.read_csv(path, comment="#")
    result: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for _, row in df.iterrows():
        result[row["gas"]][row["source"]] += float(row["emissions_mtco2eq"])
    return {gas: dict(srcs) for gas, srcs in result.items()}


def plot_emissions_breakdown(
    emissions: dict[str, dict[str, float]],
    faostat_emissions: dict[str, dict[str, float]],
    gleam_emissions: dict[str, dict[str, float]] | None,
    output_path: Path,
) -> None:
    """Create side-by-side stacked bar plots for each gas in CO2eq units, comparing modeled, FAOSTAT, and GLEAM.

    Parameters
    ----------
    emissions : dict[str, dict[str, float]]
        Modeled emissions data by gas and source (all in MtCO2eq)
    faostat_emissions : dict[str, dict[str, float]]
        FAOSTAT actual emissions data by gas and source (all in MtCO2eq)
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

    # Define gas-specific base colormaps
    gas_cmaps = {
        "CO2": "Greys",
        "CH4": "Greens",
        "N2O": "Oranges",
    }

    # Iterate through gases
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

        # Get colormap for this gas
        cmap_name = gas_cmaps.get(gas, "Blues")
        cmap = matplotlib.colormaps[cmap_name]

        # Generate a range of colors for categories within this gas
        if n_cats <= 1:
            colors_for_categories = [cmap(0.6)]
        else:
            # Use a range from 0.3 to 0.9 for shades, so largest gets darkest (0.9)
            colors_for_categories = [
                cmap(0.3 + 0.6 * i / (n_cats - 1)) for i in range(n_cats)
            ]
        colors_for_categories.reverse()  # Largest value gets darker shade
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

        # Set title and labels
        ax.set_title(gas, fontsize=14, fontweight="bold")
        if idx == 0:
            ax.set_ylabel("Emissions (MtCO₂eq)", fontsize=12)
        xticks = [x_modeled, x_actual]
        xticklabels = ["Modeled", "FAOSTAT"]
        if gleam_emissions is not None:
            xticks.append(x_gleam)
            xticklabels.append("GLEAM\n(livestock)")
        ax.set_xticks(xticks)
        ax.set_xticklabels(xticklabels, fontsize=10)
        # Add some horizontal padding so bars sit centered under their labels
        xmin = x_modeled - bar_width * 0.75
        xmax = (
            x_gleam + bar_width * 0.75
            if gleam_emissions is not None
            else x_actual + bar_width * 0.75
        )
        ax.set_xlim(xmin, xmax)

        # Add gridlines
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.axhline(y=0, color="black", linewidth=0.8)

        # Add individual legend for this gas, sorted in reverse
        # Filter out empty labels from the bars to avoid duplicate entries
        handles, labels = ax.get_legend_handles_labels()
        unique_labels = []
        unique_handles = []
        for handle, label in zip(handles, labels):
            if label and label not in unique_labels:
                unique_labels.append(label)
                unique_handles.append(handle)

        # Sort unique_labels based on the order of all_sources
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

    # Adjust layout to prevent legends from overlapping
    plt.tight_layout()
    plt.subplots_adjust(
        bottom=0.35, wspace=0.3
    )  # Increase bottom margin significantly for legends

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote emissions breakdown plot to %s", output_path)


def save_emissions_table(
    emissions: dict[str, dict[str, float]],
    output_path: Path,
) -> None:
    """Save emissions data as a CSV table.

    Parameters
    ----------
    emissions : dict[str, dict[str, float]]
        Emissions data by gas and source (all in MtCO2eq)
    output_path : Path
        Path to save the CSV file
    """
    # Convert nested dict to DataFrame
    rows = []
    for gas, sources in emissions.items():
        for source, amount in sources.items():
            rows.append(
                {
                    "gas": gas,
                    "source": source,
                    "emissions_mtco2eq": amount,
                }
            )

    df = pd.DataFrame(rows)

    if df.empty:
        df = pd.DataFrame(columns=["gas", "source", "emissions_mtco2eq"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info("Wrote emissions breakdown table to %s", output_path)


def plot_emissions_choropleth(
    emissions_by_region: pd.DataFrame,
    regions_path: str,
    output_path: Path,
) -> None:
    """Create a three-panel choropleth map showing CO2, CH4, N2O emission intensity by region.

    Parameters
    ----------
    emissions_by_region : pd.DataFrame
        DataFrame with region as index and CO2, CH4, N2O (MtCO2eq), land_area (Mha) columns
    regions_path : str
        Path to regions GeoJSON file
    output_path : Path
        Path to save the PDF plot
    """
    # Load regions GeoDataFrame
    gdf = gpd.read_file(regions_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)
    else:
        gdf = gdf.to_crs(4326)

    if "region" not in gdf.columns:
        raise ValueError("Regions GeoDataFrame must contain a 'region' column")

    gdf = gdf.set_index("region")

    # Merge emissions data with geometries
    gdf = gdf.join(emissions_by_region, how="left")
    gdf = gdf.fillna(0.0)

    # Calculate emission intensities (tCO2eq/ha = MtCO2eq/Mha)
    # Avoid division by zero
    land_area = gdf["land_area"].replace(0, float("nan"))
    gdf["CO2_intensity"] = gdf["CO2"] / land_area
    gdf["CH4_intensity"] = gdf["CH4"] / land_area
    gdf["N2O_intensity"] = gdf["N2O"] / land_area

    # Create figure with three vertically stacked panels
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(12, 14),
        subplot_kw={"projection": ccrs.EqualEarth()},
    )

    # Gas configuration
    gases = ["CO2", "CH4", "N2O"]
    gas_labels = {
        "CO2": "CO₂",
        "CH4": "CH₄",
        "N2O": "N₂O",
    }

    # Compute shared color scale across all gases using 99th percentile
    all_intensities = []
    for gas in gases:
        intensity_col = f"{gas}_intensity"
        valid = gdf[intensity_col].dropna()
        valid = valid[valid > 0]
        all_intensities.extend(valid.tolist())

    if all_intensities:
        vmin = 0
        vmax = np.percentile(all_intensities, 99)
    else:
        vmin, vmax = 0, 1

    cmap = plt.colormaps["Reds"]
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    plate = ccrs.PlateCarree()

    for idx, gas in enumerate(gases):
        ax = axes[idx]
        ax.set_facecolor("#f7f9fb")
        ax.set_global()

        intensity_col = f"{gas}_intensity"

        # Plot all regions with no data in light gray first
        ax.add_geometries(
            gdf.geometry,
            crs=plate,
            facecolor="#e0e0e0",
            edgecolor="none",
            zorder=1,
        )

        # Plot regions with intensity data
        for _region, row in gdf.iterrows():
            intensity_val = row[intensity_col]
            if pd.notna(intensity_val) and intensity_val > 0:
                color = cmap(norm(intensity_val))
                ax.add_geometries(
                    [row.geometry],
                    crs=plate,
                    facecolor=color,
                    edgecolor="none",
                    zorder=2,
                )

        # Add gridlines
        gl = ax.gridlines(draw_labels=False, linewidth=0.4, color="#aaaaaa", alpha=0.5)
        gl.xlocator = plt.MultipleLocator(60)
        gl.ylocator = plt.MultipleLocator(30)

        ax.set_title(
            f"{gas_labels[gas]} Emission Intensity", fontsize=12, fontweight="bold"
        )

    # Adjust layout to leave space for colorbar
    fig.subplots_adjust(bottom=0.08, hspace=0.1)

    # Add single shared colorbar at bottom
    cbar_ax = fig.add_axes([0.25, 0.02, 0.5, 0.015])
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal", extend="max")
    cbar.set_label("Emission intensity (tCO₂eq/ha)", fontsize=10)
    cbar.ax.tick_params(labelsize=8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    logger.info("Wrote emissions choropleth map to %s", output_path)


if __name__ == "__main__":
    logger = setup_script_logging(snakemake.log[0])

    network = pypsa.Network(snakemake.input.network)
    ch4_gwp = float(snakemake.params.ch4_gwp)
    n2o_gwp = float(snakemake.params.n2o_gwp)

    logger.info("Extracting emissions from network using energy balance statistics")
    emissions = extract_emissions_by_source(network, ch4_gwp, n2o_gwp)

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
    csv_path = Path(snakemake.output.csv)
    choropleth_path = Path(snakemake.output.choropleth_pdf)

    save_emissions_table(emissions, csv_path)
    plot_emissions_breakdown(
        emissions,
        faostat_emissions_processed,
        gleam_emissions,
        pdf_path,
    )

    # Load regions GeoDataFrame for emissions extraction
    logger.info("Loading regions from %s", snakemake.input.regions)
    regions_gdf = gpd.read_file(snakemake.input.regions)
    if regions_gdf.crs is None:
        regions_gdf = regions_gdf.set_crs(4326, allow_override=True)
    else:
        regions_gdf = regions_gdf.to_crs(4326)

    # Extract emissions by region and plot choropleth
    logger.info("Extracting emissions by region for choropleth map")
    emissions_by_region = extract_emissions_by_region(
        network, ch4_gwp, n2o_gwp, regions_gdf
    )
    logger.info("Found emissions for %d regions", len(emissions_by_region))

    plot_emissions_choropleth(
        emissions_by_region,
        snakemake.input.regions,
        choropleth_path,
    )
