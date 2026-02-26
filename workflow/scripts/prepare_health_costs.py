# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pre-compute health data for SOS2 linearisation in the solver."""

from collections.abc import Iterable
import logging
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from workflow.scripts import constants
from workflow.scripts.logging_config import setup_script_logging

AGE_BUCKETS = [
    "<1",
    "1-4",
    "5-9",
    "10-14",
    "15-19",
    "20-24",
    "25-29",
    "30-34",
    "35-39",
    "40-44",
    "45-49",
    "50-54",
    "55-59",
    "60-64",
    "65-69",
    "70-74",
    "75-79",
    "80-84",
    "85-89",
    "90-94",
    "95+",
]


# Age utilities
def _age_bucket_min(age: str) -> int:
    """Return the lower bound of an age bucket label like '25-29' or '95+'."""
    age = str(age)
    if age.startswith("<"):
        return 0
    if "-" in age:
        return int(age.split("-")[0])
    if age.endswith("+"):
        return int(age.rstrip("+"))
    return 0


# Logger will be configured in __main__ block
logger = logging.getLogger(__name__)


def _load_life_expectancy(path: str) -> pd.Series:
    """Load processed life expectancy data from prepare_life_table.py output."""
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("Life table file is empty")

    required_cols = {"age", "life_exp"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Life table missing required columns: {required_cols}")

    # Validate all expected age buckets are present
    missing = [bucket for bucket in AGE_BUCKETS if bucket not in df["age"].values]
    if missing:
        raise ValueError(
            "Life table missing life expectancy entries for age buckets: "
            + ", ".join(missing)
        )

    series = df.set_index("age")["life_exp"]
    series.name = "life_exp"
    return series


def _build_country_clusters(
    regions_path: str,
    countries: Iterable[str],
    n_clusters: int,
    population: pd.DataFrame | None = None,
    gdp_per_capita: pd.DataFrame | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[pd.Series, dict[int, list[str]]]:
    """
    Cluster countries into health regions using multi-objective criteria.

    Objectives (controlled by weights):
    - geography: Geographic proximity (minimize spatial spread)
    - gdp: GDP per capita similarity (group similar economies)
    - population: Population balance (equalize total population across clusters)

    Parameters
    ----------
    regions_path : str
        Path to GeoJSON file with country boundaries
    countries : Iterable[str]
        ISO3 country codes to include
    n_clusters : int
        Target number of clusters
    population : pd.DataFrame, optional
        Population data with columns: country, value (in thousands)
    gdp_per_capita : pd.DataFrame, optional
        GDP per capita data with columns: iso3, gdp_per_capita
    weights : dict, optional
        Weights for clustering objectives: geography, gdp, population

    Returns
    -------
    cluster_series : pd.Series
        Country ISO3 codes as index, cluster IDs as values
    cluster_to_countries : dict
        Mapping from cluster ID to list of country ISO3 codes
    """
    if weights is None:
        weights = {"geography": 1.0, "gdp": 0.0, "population": 0.0}

    regions = gpd.read_file(regions_path)

    # Project to equal-area CRS and compute country centroids
    regions_equal_area = regions.to_crs(6933)
    dissolved = regions_equal_area.dissolve(by="country", as_index=True)
    centroids = dissolved.geometry.centroid
    country_order = list(dissolved.index)

    # Build geographic coordinates
    coords = np.column_stack([centroids.x.values, centroids.y.values])

    k = max(1, min(int(n_clusters), len(coords)))
    if k < int(n_clusters):
        logger.info(
            f"Requested {n_clusters} clusters but only {len(coords)} countries available; using {k}."
        )

    if len(coords) == 1:
        labels = np.array([0])
    else:
        # Build multi-objective feature matrix
        features = _build_clustering_features(
            coords, country_order, gdp_per_capita, weights
        )

        km = KMeans(n_clusters=k, n_init=20, random_state=0)
        labels = km.fit_predict(features)

        # Apply population balance refinement if weight > 0
        pop_weight = weights["population"]
        if pop_weight > 0 and population is not None:
            labels = _refine_population_balance(
                labels, country_order, population, coords, pop_weight
            )

    dissolved["health_cluster"] = labels
    cluster_series = dissolved["health_cluster"].astype(int)
    grouped = cluster_series.groupby(cluster_series).groups
    cluster_to_countries = {
        int(cluster): sorted(indexes) for cluster, indexes in grouped.items()
    }

    # Log cluster statistics
    _log_cluster_statistics(
        cluster_series, cluster_to_countries, population, gdp_per_capita
    )

    return cluster_series, cluster_to_countries


def _build_clustering_features(
    coords: np.ndarray,
    country_order: list[str],
    gdp_per_capita: pd.DataFrame | None,
    weights: dict[str, float],
) -> np.ndarray:
    """
    Build weighted feature matrix for clustering.

    Combines geographic coordinates and GDP per capita with configurable weights.
    Features are standardized before weighting to ensure comparable scales.

    GDP data is assumed complete (imputation handled in retrieve_gdp_per_capita.py).
    """
    w_geo = weights["geography"]
    w_gdp = weights["gdp"]

    # Standardize geographic coordinates
    scaler = StandardScaler()
    coords_scaled = scaler.fit_transform(coords)

    if w_gdp > 0 and gdp_per_capita is not None:
        # Map GDP to countries in order
        gdp_map = gdp_per_capita.set_index("iso3")["gdp_per_capita"]
        gdp_values = np.array([gdp_map[c] for c in country_order])

        # Log-transform to reduce skew (GDP is typically log-normal)
        gdp_log = np.log1p(gdp_values).reshape(-1, 1)
        gdp_scaled = scaler.fit_transform(gdp_log)

        # Apply weights (sqrt because K-means minimizes squared distances)
        # Geography has 2 dimensions, GDP has 1
        total_weight = 2 * w_geo + w_gdp
        geo_factor = np.sqrt(w_geo / total_weight)
        gdp_factor = np.sqrt(w_gdp / total_weight)

        features = np.column_stack(
            [
                coords_scaled * geo_factor,
                gdp_scaled * gdp_factor,
            ]
        )
    else:
        # Geography only (original behavior)
        features = coords_scaled

    return features


def _refine_population_balance(
    labels: np.ndarray,
    country_order: list[str],
    population: pd.DataFrame,
    coords: np.ndarray,
    pop_weight: float,
    max_iter: int = 100,
) -> np.ndarray:
    """
    Iteratively refine cluster assignments to improve population balance.

    Moves boundary countries from over-populated to under-populated clusters
    until the population coefficient of variation (CV) is acceptable.

    The target CV is determined by the population weight:
    - Higher weight = stricter balance requirement (lower target CV)
    """
    labels = labels.copy()

    # Get total population per country (sum across years if multiple)
    pop_by_country = (
        population[population["age"] == "all-a"].groupby("country")["value"].sum()
    )
    country_pop = np.array([pop_by_country.get(c, 0.0) for c in country_order])

    # Target CV based on population weight (higher weight = stricter balance)
    # Weight 0.3 -> target CV ~0.6, Weight 1.0 -> target CV ~0.3
    target_cv = max(0.2, 0.8 - 0.5 * pop_weight)

    for iteration in range(max_iter):
        # Compute cluster populations
        cluster_ids = np.unique(labels)
        cluster_pops = {cid: country_pop[labels == cid].sum() for cid in cluster_ids}

        # Compute coefficient of variation
        pop_values = np.array(list(cluster_pops.values()))
        if pop_values.mean() == 0:
            break
        cv = pop_values.std() / pop_values.mean()

        if cv <= target_cv:
            logger.info(
                f"Population balance achieved after {iteration} iterations "
                f"(CV={cv:.3f}, target={target_cv:.3f})"
            )
            break

        # Find most over-populated and under-populated clusters
        max_cluster = max(cluster_pops, key=cluster_pops.get)
        min_cluster = min(cluster_pops, key=cluster_pops.get)

        if max_cluster == min_cluster:
            break

        # Find boundary country in over-populated cluster (furthest from centroid)
        in_max = np.where(labels == max_cluster)[0]
        if len(in_max) <= 1:
            # Can't remove from a single-country cluster
            break

        cluster_coords = coords[in_max]
        centroid = cluster_coords.mean(axis=0)
        dists = np.linalg.norm(cluster_coords - centroid, axis=1)
        boundary_local_idx = dists.argmax()
        boundary_idx = in_max[boundary_local_idx]

        # Reassign to under-populated cluster
        labels[boundary_idx] = min_cluster
    else:
        logger.info(
            f"Population balance refinement reached max iterations "
            f"(CV={cv:.3f}, target={target_cv:.3f})"
        )

    return labels


def _log_cluster_statistics(
    cluster_series: pd.Series,
    cluster_to_countries: dict[int, list[str]],
    population: pd.DataFrame | None,
    gdp_per_capita: pd.DataFrame | None,
) -> None:
    """Log summary statistics about the clustering result."""
    n_clusters = len(cluster_to_countries)
    n_countries = len(cluster_series)
    logger.info(f"Created {n_clusters} health clusters from {n_countries} countries")

    if population is not None:
        pop_by_country = (
            population[population["age"] == "all-a"].groupby("country")["value"].sum()
        )
        cluster_pops = []
        for members in cluster_to_countries.values():
            cluster_pop = sum(pop_by_country.get(c, 0.0) for c in members)
            cluster_pops.append(cluster_pop)

        if cluster_pops:
            pop_arr = np.array(cluster_pops) * 1000  # Convert to persons
            cv = pop_arr.std() / pop_arr.mean() if pop_arr.mean() > 0 else 0
            logger.info(
                f"Cluster population stats: min={pop_arr.min() / 1e6:.1f}M, "
                f"max={pop_arr.max() / 1e6:.1f}M, CV={cv:.3f}"
            )

    if gdp_per_capita is not None:
        gdp_map = gdp_per_capita.set_index("iso3")["gdp_per_capita"]
        for cluster_id, members in list(cluster_to_countries.items())[:3]:
            gdp_values = [gdp_map.get(c) for c in members if c in gdp_map.index]
            if gdp_values:
                gdp_arr = np.array(gdp_values)
                logger.info(
                    f"Cluster {cluster_id}: {len(members)} countries, "
                    f"GDP/cap ${gdp_arr.mean():,.0f} (std=${gdp_arr.std():,.0f})"
                )


class RelativeRiskTable(dict[tuple[str, str], dict[str, np.ndarray]]):
    """Container mapping (risk, cause) to exposure grids and log RR values."""


def _build_rr_tables(
    rr_df: pd.DataFrame,
    risk_factors: Iterable[str],
    risk_cause_map: dict[str, list[str]],
) -> tuple[RelativeRiskTable, dict[str, float]]:
    """Build lookup tables for relative risk curves by (risk, cause) pairs.

    Returns:
        table: Dict mapping (risk, cause) to exposure arrays and log(RR) values
        max_exposure_g_per_day: Dict mapping risk factor to maximum exposure level in data
    """
    table: RelativeRiskTable = RelativeRiskTable()
    max_exposure_g_per_day: dict[str, float] = dict.fromkeys(risk_factors, 0.0)
    allowed = {(risk, cause) for risk in risk_factors for cause in risk_cause_map[risk]}
    seen_pairs: set[tuple[str, str]] = set()
    seen_risks: set[str] = set()

    for (risk, cause), grp in rr_df.groupby(["risk_factor", "cause"], sort=True):
        if (risk, cause) not in allowed:
            continue

        grp = grp.sort_values("exposure_g_per_day")
        exposures = grp["exposure_g_per_day"].astype(float).values
        if len(exposures) == 0:
            continue
        log_rr_mean = np.log(grp["rr_mean"].astype(float).values)
        log_rr_low = np.log(grp["rr_low"].astype(float).values)
        log_rr_high = np.log(grp["rr_high"].astype(float).values)

        table[(risk, cause)] = {
            "exposures": exposures,
            "log_rr_mean": log_rr_mean,
            "log_rr_low": log_rr_low,
            "log_rr_high": log_rr_high,
        }
        max_exposure_g_per_day[risk] = max(
            max_exposure_g_per_day[risk], float(exposures.max())
        )
        seen_risks.add(risk)
        seen_pairs.add((risk, cause))

    missing_pairs = sorted(allowed - seen_pairs)
    if missing_pairs:
        text = ", ".join([f"{r}:{c}" for r, c in missing_pairs])
        raise ValueError(f"Relative risk table is missing risk-cause pairs: {text}")

    return table, max_exposure_g_per_day


def _derive_tmrel_from_rr(
    rr_lookup: RelativeRiskTable, risk_to_causes: dict[str, list[str]]
) -> dict[str, float]:
    """Derive TMREL intake per risk from empirical RR curves.

    For each risk, find the intake that minimizes the sum of log(RR) across all
    its causes (i.e., the product of RRs), evaluated on the union of exposure
    knots in the RR tables.
    """
    tmrel: dict[str, float] = {}
    for risk, causes in risk_to_causes.items():
        exposure_grid: list[float] = []
        for cause in causes:
            exposure_grid.extend(rr_lookup[(risk, cause)]["exposures"])
        if not exposure_grid:
            raise ValueError(f"No RR exposure data for risk factor: {risk}")
        grid = sorted({float(x) for x in exposure_grid})
        best_intake = grid[0]
        best_log = math.inf
        for intake in grid:
            total_log = 0.0
            for cause in causes:
                total_log += math.log(_evaluate_rr(rr_lookup, risk, cause, intake))
            if total_log < best_log:
                best_log = total_log
                best_intake = intake
        tmrel[risk] = best_intake
    return tmrel


def _evaluate_rr(
    table: RelativeRiskTable,
    risk: str,
    cause: str,
    intake: float,
    key: str = "log_rr_mean",
) -> float:
    """Interpolate relative risk for given intake using log-linear interpolation."""
    data = table[(risk, cause)]
    exposures: npt.NDArray[np.floating] = data["exposures"]
    log_rr: npt.NDArray[np.floating] = data[key]

    if intake <= exposures[0]:
        return float(math.exp(log_rr[0]))
    if intake >= exposures[-1]:
        return float(math.exp(log_rr[-1]))

    log_val = float(np.interp(intake, exposures, log_rr))
    return float(math.exp(log_val))


def _load_input_data(
    snakemake,
    cfg_countries: list[str],
    reference_year: int,
) -> tuple:
    """Load and perform initial processing of all input datasets."""
    # Load population data first (needed for clustering)
    pop = pd.read_csv(snakemake.input["population"])
    pop["value"] = pd.to_numeric(pop["value"], errors="coerce") / 1_000.0

    # Load GDP per capita data
    gdp_per_capita = pd.read_csv(snakemake.input["gdp"])

    # Get clustering weights from config
    health_cfg = snakemake.params["health"]
    clustering_cfg = health_cfg["clustering"]
    weights = clustering_cfg["weights"]

    cluster_series, cluster_to_countries = _build_country_clusters(
        snakemake.input["regions"],
        cfg_countries,
        int(health_cfg["region_clusters"]),
        population=pop,
        gdp_per_capita=gdp_per_capita,
        weights=weights,
    )

    cluster_map = cluster_series.rename("health_cluster").reset_index()
    cluster_map.columns = ["country_iso3", "health_cluster"]
    cluster_map = cluster_map.sort_values("country_iso3")

    diet = pd.read_csv(snakemake.input["diet"])
    rr_df = pd.read_csv(snakemake.input["relative_risks"])
    dr = pd.read_csv(
        snakemake.input["dr"],
        header=None,
        names=["age", "cause", "country", "year", "value"],
    )
    life_exp = _load_life_expectancy(snakemake.input["life_table"])

    return (
        cluster_series,
        cluster_to_countries,
        cluster_map,
        diet,
        rr_df,
        dr,
        pop,
        life_exp,
    )


def _filter_and_prepare_data(
    diet: pd.DataFrame,
    dr: pd.DataFrame,
    pop: pd.DataFrame,
    rr_df: pd.DataFrame,
    cfg_countries: list[str],
    reference_year: int,
    life_exp: pd.Series,
    risk_factors: list[str],
    risk_cause_map: dict[str, list[str]],
    intake_age_min: int,
) -> tuple:
    """Filter datasets to reference year and compute derived quantities."""
    # Filter dietary intake data to adult buckets and compute population-weighted means
    adult_ages = {
        age for age in diet["age"].unique() if _age_bucket_min(age) >= intake_age_min
    }
    diet = diet[
        (diet["age"].isin(adult_ages))
        & (diet["year"] == reference_year)
        & (diet["country"].isin(cfg_countries))
    ].copy()

    # Build relative risk lookup tables
    rr_lookup, max_exposure_g_per_day = _build_rr_tables(
        rr_df, risk_factors, risk_cause_map
    )

    # Filter mortality and population data
    dr = dr[(dr["year"] == reference_year) & (dr["country"].isin(cfg_countries))].copy()
    pop = pop[
        (pop["year"] == reference_year) & (pop["country"].isin(cfg_countries))
    ].copy()

    valid_ages = life_exp.index
    dr = dr[dr["age"].isin(valid_ages)].copy()
    pop_age = pop[pop["age"].isin(valid_ages)].copy()

    pop_total = (
        pop[pop["age"] == "all-a"]
        .groupby("country")["value"]
        .sum()
        .astype(float)
        .reindex(cfg_countries)
    )

    # Determine relevant risk-cause pairs
    risk_to_causes = {risk: list(risk_cause_map[risk]) for risk in risk_factors}
    relevant_causes = sorted(
        {cause for causes in risk_to_causes.values() for cause in causes}
    )

    dr = dr[dr["cause"].isin(relevant_causes)].copy()

    # Map diet items to risk factors
    item_to_risk = {
        "whole_grains": "whole_grains",
        "legumes": "legumes",
        "soybeans": "legumes",
        "nuts_seeds": "nuts_seeds",
        "vegetables": "vegetables",
        "fruits_trop": "fruits",
        "fruits_temp": "fruits",
        "fruits_starch": "fruits",
        "fruits": "fruits",
        "beef": "red_meat",
        "lamb": "red_meat",
        "pork": "red_meat",
        "red_meat": "red_meat",
        "sugar": "sugar",
    }
    diet["risk_factor"] = diet["item"].map(item_to_risk)
    diet = diet.dropna(subset=["risk_factor"])
    # Population-weighted adult intakes per country and risk factor
    # The dietary intake file is already aggregated to adult bands ("11-74 years", "75+ years").
    # Population file is per narrow age band, so collapse to total adult population per country.
    pop_adult = (
        pop_age[pop_age["age"].isin(adult_ages)]
        .groupby("country")["value"]
        .sum()
        .astype(float)
        .rename("population_adult")
    )
    if pop_adult.isna().any() or (pop_adult <= 0).any():
        raise ValueError("Adult population totals are missing or non-positive")

    diet = diet.rename(columns={"value": "intake"})
    diet["intake"] = pd.to_numeric(diet["intake"], errors="coerce")
    if diet["intake"].isna().any():
        raise ValueError("Dietary intake contains non-numeric values")

    # For each country/risk, take the adult-age mean intake and weight by total adult population
    diet_grouped = (
        diet.groupby(["country", "risk_factor"])["intake"].mean().rename("intake_mean")
    )
    intake_by_country = diet_grouped.unstack(fill_value=0.0).reindex(
        cfg_countries, fill_value=0.0
    )

    return (
        dr,
        pop_age,
        pop_total,
        rr_lookup,
        max_exposure_g_per_day,
        relevant_causes,
        risk_to_causes,
        intake_by_country,
    )


def _compute_baseline_health_metrics(
    dr: pd.DataFrame,
    pop_age: pd.DataFrame,
    life_exp: pd.Series,
) -> pd.DataFrame:
    """Compute baseline death counts and YLL statistics by country."""
    pop_age = pop_age.rename(columns={"value": "population"})
    dr = dr.rename(columns={"value": "death_rate"})
    combo = dr.merge(pop_age, on=["age", "country", "year"], how="left").merge(
        life_exp.rename("life_exp"), left_on="age", right_index=True, how="left"
    )
    combo["population"] = combo["population"].fillna(0.0)
    combo["death_rate"] = combo["death_rate"].fillna(0.0)
    combo["death_count"] = combo["death_rate"] * combo["population"]
    combo["yll"] = combo["death_count"] * combo["life_exp"]

    return combo


def _build_intake_caps(
    max_exposure_g_per_day: dict[str, float],
    intake_cap_limit: float,
) -> dict[str, float]:
    """Apply a uniform generous intake cap across all risk factors."""

    if intake_cap_limit <= 0:
        return dict(max_exposure_g_per_day)

    caps = dict(max_exposure_g_per_day)
    for risk in list(caps.keys()):
        caps[risk] = max(caps[risk], float(intake_cap_limit))
    return caps


def _process_health_clusters(
    cluster_to_countries: dict[int, list[str]],
    pop_total: pd.Series,
    combo: pd.DataFrame,
    risk_factors: list[str],
    intake_by_country: pd.DataFrame,
    intake_caps_g_per_day: dict[str, float],
    rr_lookup: RelativeRiskTable,
    risk_to_causes: dict[str, list[str]],
    relevant_causes: list[str],
    tmrel_g_per_day: dict[str, float],
    reference_year: int,
) -> tuple:
    """Process each health cluster to compute baseline metrics and intakes.

    Returns YLL as incidence rates (per 100,000 population) rather than
    absolute values, enabling reconstruction using scenario-appropriate
    population data during model building/solving.
    """
    cluster_summary_rows = []
    cluster_cause_rows = []
    cluster_risk_baseline_rows = []
    baseline_intake_registry: dict[str, set] = {risk: set() for risk in risk_factors}

    for cluster_id, members in cluster_to_countries.items():
        pop_weights = pop_total.reindex(members).fillna(0.0)
        total_pop_thousand = float(pop_weights.sum())
        if total_pop_thousand <= 0:
            continue

        total_population_persons = total_pop_thousand * 1_000.0
        cluster_combo = combo[combo["country"].isin(members)]
        yll_by_cause_cluster = cluster_combo.groupby("cause")["yll"].sum()

        cluster_summary_rows.append(
            {
                "health_cluster": int(cluster_id),
                "reference_population": total_population_persons,
                "reference_year": reference_year,
            }
        )

        log_rr_ref_totals: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)
        log_rr_baseline_totals: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)

        for risk in risk_factors:
            if risk not in intake_by_country.columns:
                baseline_intake = 0.0
            else:
                baseline_intake = (
                    intake_by_country[risk].reindex(members).fillna(0.0) * pop_weights
                ).sum() / total_pop_thousand
            baseline_intake = float(baseline_intake)
            if not math.isfinite(baseline_intake):
                baseline_intake = 0.0
            max_exposure = float(intake_caps_g_per_day[risk])
            baseline_intake = max(0.0, min(baseline_intake, max_exposure))
            baseline_intake_registry.setdefault(risk, set()).add(baseline_intake)

            cluster_risk_baseline_rows.append(
                {
                    "health_cluster": int(cluster_id),
                    "risk_factor": risk,
                    "baseline_intake_g_per_day": baseline_intake,
                }
            )

            # Use TMREL intake as reference point for health cost calculations.
            # rr_ref is calculated at TMREL, ensuring that in the solver, health cost
            # is zero when RR = RR_ref (i.e., when intake is at optimal levels).
            # This implements Cost = V * YLL_base * (RR/RR_ref - 1).
            tmrel_intake = float(tmrel_g_per_day[risk])
            if not math.isfinite(tmrel_intake):
                tmrel_intake = 0.0
            tmrel_intake = max(0.0, min(tmrel_intake, max_exposure))

            causes = risk_to_causes[risk]
            for cause in causes:
                if (risk, cause) not in rr_lookup:
                    continue
                rr_ref = _evaluate_rr(rr_lookup, risk, cause, tmrel_intake)
                log_rr_ref_totals[cause] = log_rr_ref_totals[cause] + math.log(rr_ref)

                rr_base = _evaluate_rr(rr_lookup, risk, cause, baseline_intake)
                log_rr_baseline_totals[cause] = log_rr_baseline_totals[
                    cause
                ] + math.log(rr_base)

        for cause in relevant_causes:
            log_rr_baseline = log_rr_baseline_totals[cause]
            rr_baseline = math.exp(log_rr_baseline)
            rr_ref = math.exp(log_rr_ref_totals[cause])
            paf = (
                0.0 if rr_baseline <= 0 else 1.0 - rr_ref / rr_baseline
            )  # burden relative to TMREL
            paf = max(0.0, min(1.0, paf))
            yll_total_absolute = yll_by_cause_cluster.get(cause, 0.0)

            # Convert absolute YLL to incidence rates per 100k population
            yll_rate_per_100k = (
                (yll_total_absolute / total_population_persons) * constants.PER_100K
                if total_population_persons > 0
                else 0.0
            )
            yll_attrib_rate_per_100k = yll_rate_per_100k * paf

            cluster_cause_rows.append(
                {
                    "health_cluster": int(cluster_id),
                    "cause": cause,
                    "log_rr_total_ref": log_rr_ref_totals[cause],
                    "log_rr_total_baseline": log_rr_baseline,
                    "paf_baseline": paf,
                    "yll_rate_per_100k": yll_rate_per_100k,
                    "yll_attrib_rate_per_100k": yll_attrib_rate_per_100k,
                }
            )

    cluster_summary = pd.DataFrame(
        cluster_summary_rows,
        columns=["health_cluster", "reference_population", "reference_year"],
    )
    cluster_cause_baseline = pd.DataFrame(
        cluster_cause_rows,
        columns=[
            "health_cluster",
            "cause",
            "log_rr_total_ref",
            "log_rr_total_baseline",
            "paf_baseline",
            "yll_rate_per_100k",
            "yll_attrib_rate_per_100k",
        ],
    )
    cluster_risk_baseline = pd.DataFrame(
        cluster_risk_baseline_rows,
        columns=["health_cluster", "risk_factor", "baseline_intake_g_per_day"],
    )

    return (
        cluster_summary,
        cluster_cause_baseline,
        cluster_risk_baseline,
        baseline_intake_registry,
    )


def _generate_breakpoint_tables(
    risk_factors: list[str],
    intake_caps_g_per_day: dict[str, float],
    baseline_intake_registry: dict[str, set],
    intake_grid_points: int,
    rr_lookup: RelativeRiskTable,
    risk_to_causes: dict[str, list[str]],
    relevant_causes: list[str],
    log_rr_points: int,
    tmrel_g_per_day: dict[str, float],
) -> tuple:
    """Generate SOS2 linearization breakpoint tables for risks and causes.

    Intake grids:
        - Evenly spaced `intake_grid_points` over the empirical RR data range
          (min→max exposure in RR table, expanded to include 0).
        - Always include all empirical exposure points, TMREL, baseline intakes,
          and the global intake cap for feasibility beyond the data range.
        - The generous cap is *added* as a knot but does not stretch the
          linspace; this keeps knot density high where RR actually changes.
    Cause grids:
        - `log_rr_points` evenly spaced between aggregated min/max log(RR)
          implied by the risk grids above.
    """
    risk_breakpoint_rows = []
    cause_log_min: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)
    cause_log_max: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)

    for risk in risk_factors:
        cap = float(intake_caps_g_per_day[risk])
        if cap <= 0:
            continue
        causes = risk_to_causes[risk]
        # Empirical exposure domain from RR table (may vary by cause; take union)
        exposures = []
        for cause in causes:
            exposures = rr_lookup[(risk, cause)]["exposures"]
            if exposures is not None:
                exposures = list(exposures)
                break
        if not exposures:
            continue
        lo = min(0.0, float(min(exposures)))
        hi_empirical = float(max(exposures))
        # Even spacing only over the empirical RR range
        lin = np.linspace(lo, hi_empirical, max(intake_grid_points, 2))
        grid_points = {float(x) for x in lin}
        grid_points.update(float(x) for x in exposures)
        grid_points.add(0.0)
        grid_points.add(hi_empirical)
        for val in baseline_intake_registry[risk]:
            grid_points.add(float(val))
        # Include TMREL as a breakpoint for accurate interpolation at optimal intake
        if risk in tmrel_g_per_day:
            grid_points.add(float(tmrel_g_per_day[risk]))
        # Add the generous cap without stretching the linspace range
        grid_points.add(cap)
        grid = sorted(grid_points)

        for cause in causes:
            key = (risk, cause)
            if key not in rr_lookup:
                continue
            log_values: list[float] = []
            log_values_low: list[float] = []
            log_values_high: list[float] = []
            for intake in grid:
                rr_val = _evaluate_rr(rr_lookup, risk, cause, intake)
                log_rr = math.log(rr_val)
                rr_val_low = _evaluate_rr(
                    rr_lookup, risk, cause, intake, key="log_rr_low"
                )
                log_rr_low = math.log(rr_val_low)
                rr_val_high = _evaluate_rr(
                    rr_lookup, risk, cause, intake, key="log_rr_high"
                )
                log_rr_high = math.log(rr_val_high)
                log_values.append(log_rr)
                log_values_low.append(log_rr_low)
                log_values_high.append(log_rr_high)
                risk_breakpoint_rows.append(
                    {
                        "risk_factor": risk,
                        "cause": cause,
                        "intake_g_per_day": float(intake),
                        "log_rr": log_rr,
                        "log_rr_low": log_rr_low,
                        "log_rr_high": log_rr_high,
                    }
                )
            if log_values:
                # Use the most extreme bounds across mean, low, and high
                # to ensure cause breakpoints cover the full uncertainty range
                all_log = log_values + log_values_low + log_values_high
                cause_log_min[cause] += min(all_log)
                cause_log_max[cause] += max(all_log)

    risk_breakpoints = pd.DataFrame(risk_breakpoint_rows)

    cause_breakpoint_rows = []
    for cause in relevant_causes:
        min_total = cause_log_min[cause]
        max_total = cause_log_max[cause]
        if not math.isfinite(min_total):
            min_total = 0.0
        if not math.isfinite(max_total):
            max_total = 0.0
        if max_total < min_total:
            min_total, max_total = max_total, min_total
        if abs(max_total - min_total) < 1e-6:
            log_vals = np.array([min_total])
        else:
            log_vals = np.linspace(min_total, max_total, max(log_rr_points, 2))
        for log_val in log_vals:
            cause_breakpoint_rows.append(
                {
                    "cause": cause,
                    "log_rr_total": float(log_val),
                    "rr_total": math.exp(float(log_val)),
                }
            )

    cause_log_breakpoints = pd.DataFrame(cause_breakpoint_rows)

    return risk_breakpoints, cause_log_breakpoints


def main() -> None:
    """Main entry point for health cost preparation."""
    logger = logging.getLogger(__name__)

    cfg_countries: list[str] = list(snakemake.params["countries"])
    health_cfg = snakemake.params["health"]
    configured_risk_factors: list[str] = list(health_cfg["risk_factors"])

    # Filter risk factors to only those with foods mapped in food_groups.csv
    food_groups_df = pd.read_csv(snakemake.input.food_groups)
    available_risk_factors = set(food_groups_df["group"].unique())
    risk_factors: list[str] = [
        rf for rf in configured_risk_factors if rf in available_risk_factors
    ]
    excluded_risk_factors = set(configured_risk_factors) - set(risk_factors)
    if excluded_risk_factors:
        logger.warning(
            "Risk factors configured but not in food_groups.csv (no foods mapped): %s. "
            "These will be excluded from health cost calculations.",
            sorted(excluded_risk_factors),
        )

    risk_cause_map: dict[str, list[str]] = {
        str(risk): list(health_cfg["risk_cause_map"][risk]) for risk in risk_factors
    }
    reference_year = int(health_cfg["reference_year"])
    intake_grid_points = int(health_cfg["intake_grid_points"])
    log_rr_points = int(health_cfg["log_rr_points"])
    intake_cap_limit = float(health_cfg["intake_cap_g_per_day"])
    intake_age_min = int(health_cfg["intake_age_min"])

    # Load input data
    (
        _cluster_series,
        cluster_to_countries,
        cluster_map,
        diet,
        rr_df,
        dr,
        pop,
        life_exp,
    ) = _load_input_data(snakemake, cfg_countries, reference_year)

    # Filter and prepare datasets
    (
        dr,
        pop_age,
        pop_total,
        rr_lookup,
        max_exposure_g_per_day,
        relevant_causes,
        risk_to_causes,
        intake_by_country,
    ) = _filter_and_prepare_data(
        diet,
        dr,
        pop,
        rr_df,
        cfg_countries,
        reference_year,
        life_exp,
        risk_factors,
        risk_cause_map,
        intake_age_min,
    )

    tmrel_g_per_day = _derive_tmrel_from_rr(rr_lookup, risk_to_causes)
    logger.info(
        "Derived TMREL from RR curves for %d risks",
        len(tmrel_g_per_day),
    )

    intake_caps_g_per_day = _build_intake_caps(max_exposure_g_per_day, intake_cap_limit)

    # Compute baseline health metrics
    combo = _compute_baseline_health_metrics(
        dr,
        pop_age,
        life_exp,
    )

    # Process health clusters
    (
        cluster_summary,
        cluster_cause_baseline,
        cluster_risk_baseline,
        baseline_intake_registry,
    ) = _process_health_clusters(
        cluster_to_countries,
        pop_total,
        combo,
        risk_factors,
        intake_by_country,
        intake_caps_g_per_day,
        rr_lookup,
        risk_to_causes,
        relevant_causes,
        tmrel_g_per_day,
        reference_year,
    )

    # Generate breakpoint tables for SOS2 linearization
    risk_breakpoints, cause_log_breakpoints = _generate_breakpoint_tables(
        risk_factors,
        intake_caps_g_per_day,
        baseline_intake_registry,
        intake_grid_points,
        rr_lookup,
        risk_to_causes,
        relevant_causes,
        log_rr_points,
        tmrel_g_per_day,
    )

    # Write outputs
    output_dir = Path(snakemake.output["risk_breakpoints"]).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    risk_breakpoints.sort_values(["risk_factor", "cause", "intake_g_per_day"]).to_csv(
        snakemake.output["risk_breakpoints"], index=False
    )
    cluster_cause_baseline.sort_values(["health_cluster", "cause"]).to_csv(
        snakemake.output["cluster_cause"], index=False
    )
    cause_log_breakpoints.sort_values(["cause", "log_rr_total"]).to_csv(
        snakemake.output["cause_log"], index=False
    )
    cluster_summary.sort_values("health_cluster").to_csv(
        snakemake.output["cluster_summary"], index=False
    )
    cluster_map.to_csv(snakemake.output["clusters"], index=False)
    cluster_risk_baseline.sort_values(["health_cluster", "risk_factor"]).to_csv(
        snakemake.output["cluster_risk_baseline"], index=False
    )

    # Write derived TMREL values
    tmrel_df = pd.DataFrame(
        [
            {"risk_factor": risk, "tmrel_g_per_day": value}
            for risk, value in tmrel_g_per_day.items()
        ]
    )
    tmrel_df.sort_values("risk_factor").to_csv(
        snakemake.output["derived_tmrel"], index=False
    )


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
