#! /usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plot baseline food consumption by health cluster using pie charts."""

from collections.abc import Mapping
from pathlib import Path

import matplotlib

matplotlib.use("pdf")
import numpy as np
import pandas as pd

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.plotting.color_utils import categorical_colors
from workflow.scripts.plotting.plot_food_consumption_map import (
    _plot_cluster_pies,
    _prepare_cluster_geodata,
)


def _load_baseline_diet(
    diet_path: str,
    *,
    age: str,
    year: int | None,
    countries: Mapping[str, int],
) -> pd.DataFrame:
    diet_df = pd.read_csv(diet_path)
    diet_df["country"] = diet_df["country"].str.upper()
    df = diet_df[diet_df["country"].isin(countries.keys())]

    if age:
        df = df[df["age"] == age]
    if df.empty:
        raise ValueError(f"No baseline diet rows left after filtering age='{age}'")

    if year is not None:
        year_df = df[df["year"] == year]
        if year_df.empty:
            available_years = sorted(df["year"].dropna().unique())
            raise ValueError(
                "No baseline diet rows for year "
                f"{year}. Available years: {available_years}"
            )
        df = year_df

    pivot = df.pivot_table(
        index="country", columns="item", values="value", aggfunc="mean"
    )
    return pivot


def _cluster_population_weights(
    population_path: str,
    iso_to_cluster: Mapping[str, int],
) -> tuple[dict[int, float], dict[str, float]]:
    pop_df = pd.read_csv(population_path)
    pop_df["iso3"] = pop_df["iso3"].str.upper()
    pop_df = pop_df[pop_df["iso3"].isin(iso_to_cluster.keys())]
    grouped = pop_df.groupby("iso3")["population"].sum()
    cluster_weights: dict[int, float] = {}
    per_country = {iso: float(val) for iso, val in grouped.items() if float(val) > 0.0}
    for iso, value in grouped.items():
        cluster = iso_to_cluster.get(iso)
        if cluster is None:
            continue
        val = float(value)
        if val <= 0.0:
            continue
        cluster_weights[cluster] = cluster_weights.get(cluster, 0.0) + val
    missing_clusters = [
        c for c in set(iso_to_cluster.values()) if cluster_weights.get(c, 0.0) <= 0.0
    ]
    if missing_clusters:
        raise ValueError(
            "Missing population weights for clusters: "
            + ", ".join(map(str, sorted(missing_clusters)))
        )
    return cluster_weights, per_country


def _aggregate_cluster_diet(
    pivot: pd.DataFrame,
    iso_to_cluster: Mapping[str, int],
    cluster_population: Mapping[int, float],
    country_population: Mapping[str, float],
) -> pd.DataFrame:
    if pivot.empty:
        return pd.DataFrame()
    data: dict[tuple[int, str], float] = {}
    for iso, row in pivot.iterrows():
        iso_code = str(iso).upper()
        cluster = iso_to_cluster.get(iso_code)
        if cluster is None:
            continue
        population = country_population.get(iso_code)
        if population is None or population <= 0.0:
            continue
        for group, value in row.items():
            if value is None or not np.isfinite(value):
                continue
            key = (cluster, str(group))
            data[key] = data.get(key, 0.0) + float(value) * population
    if not data:
        return pd.DataFrame()
    cluster_df = (
        pd.Series(data).unstack(fill_value=0.0).sort_index(axis=0).sort_index(axis=1)
    )
    for cluster, values in cluster_df.iterrows():
        total_pop = cluster_population.get(cluster)
        if total_pop is None or total_pop <= 0:
            continue
        cluster_df.loc[cluster] = values / total_pop
    return cluster_df


def main() -> None:
    try:
        snakemake  # type: ignore[name-defined]
    except NameError as exc:  # pragma: no cover
        raise RuntimeError("This script must be run via Snakemake") from exc

    logger = setup_script_logging(snakemake.log[0])
    diet_path = snakemake.input.diet  # type: ignore[attr-defined]
    clusters_path = snakemake.input.clusters
    regions_path = snakemake.input.regions
    population_path = snakemake.input.population
    output_pdf = Path(snakemake.output.pdf)
    output_csv = Path(snakemake.output.csv)

    age = getattr(snakemake.params, "age", "All ages")
    reference_year = getattr(snakemake.params, "reference_year", None)
    if reference_year is not None:
        reference_year = int(reference_year)

    clusters_df = pd.read_csv(clusters_path)
    if "country_iso3" not in clusters_df or "health_cluster" not in clusters_df:
        raise ValueError(
            "Cluster table must contain 'country_iso3' and 'health_cluster'"
        )
    iso_to_cluster = (
        clusters_df.assign(country_iso3=lambda df: df["country_iso3"].str.upper())
        .set_index("country_iso3")["health_cluster"]
        .astype(int)
        .to_dict()
    )

    baseline_pivot = _load_baseline_diet(
        diet_path,
        age=age,
        year=reference_year,
        countries=iso_to_cluster,
    )

    cluster_population, country_population = _cluster_population_weights(
        population_path, iso_to_cluster
    )
    cluster_consumption = _aggregate_cluster_diet(
        baseline_pivot,
        iso_to_cluster,
        cluster_population,
        country_population,
    )
    if cluster_consumption.empty:
        raise ValueError("No baseline diet data aggregated to clusters")
    cluster_consumption = cluster_consumption.loc[cluster_consumption.sum(axis=1) > 0.0]
    cluster_consumption = cluster_consumption.loc[
        :, cluster_consumption.sum(axis=0) > 0.0
    ]
    if cluster_consumption.empty:
        raise ValueError("Baseline diet data has no positive values to plot")

    groups = cluster_consumption.sum(axis=0).sort_values(ascending=False).index.tolist()
    group_colors = getattr(snakemake.params, "group_colors", {}) or {}
    colors = categorical_colors(groups, group_colors)

    cluster_gdf, cluster_gdf_eq = _prepare_cluster_geodata(regions_path, iso_to_cluster)
    _plot_cluster_pies(
        cluster_consumption,
        cluster_gdf,
        cluster_gdf_eq,
        colors,
        output_pdf,
        radius_scale_factor=0.02,
    )

    cluster_consumption.sort_index(axis=0).sort_index(axis=1).to_csv(
        output_csv, index=True
    )
    logger.info("Saved baseline map to %s", output_pdf)


if __name__ == "__main__":
    main()
