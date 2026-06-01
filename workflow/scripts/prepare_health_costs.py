# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
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
    """Return the lower bound of an age bucket label like '25-29' or '95+'.

    The GDD-IA dietary intake table only emits adult-equivalent rows tagged
    as 'All ages' / 'all-a'. Treat those as adult-equivalent so the
    intake_age_min filter does not drop every diet observation.
    """
    age = str(age)
    if age in ("All ages", "all-a"):
        return 18
    if age.startswith("<"):
        return 0
    if "-" in age:
        return int(age.split("-")[0])
    if age.endswith("+"):
        return int(age.rstrip("+"))
    raise ValueError(f"Unrecognised age bucket label: {age!r}")


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


class RelativeRiskTable(dict[tuple[str, str, str], dict[str, np.ndarray]]):
    """Container mapping (risk, cause, age) to exposure grids and log RR values."""


# The 15 adult age groups from GBD, matching population/mortality age buckets.
ADULT_AGES: list[str] = [
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

# Type alias for age weights: maps (cluster_id, cause, age) → weight in [0, 1].
AgeWeights = dict[tuple[int, str, str], float]


def _build_rr_tables(
    rr_df: pd.DataFrame,
    risk_factors: Iterable[str],
    risk_cause_map: dict[str, list[str]],
) -> tuple[RelativeRiskTable, dict[str, float]]:
    """Build lookup tables for relative risk curves by (risk, cause, age).

    Returns:
        table: Dict mapping (risk, cause, age) to exposure arrays and log(RR) values
        max_exposure_g_per_day: Dict mapping risk factor to maximum exposure level in data
    """
    table: RelativeRiskTable = RelativeRiskTable()
    max_exposure_g_per_day: dict[str, float] = dict.fromkeys(risk_factors, 0.0)
    allowed = {(risk, cause) for risk in risk_factors for cause in risk_cause_map[risk]}
    seen_pairs: set[tuple[str, str]] = set()
    seen_risks: set[str] = set()

    for (risk, cause, age), grp in rr_df.groupby(
        ["risk_factor", "cause", "age"], sort=True
    ):
        if (risk, cause) not in allowed:
            continue

        grp = grp.sort_values("exposure_g_per_day")
        exposures = grp["exposure_g_per_day"].astype(float).values
        if len(exposures) == 0:
            continue
        log_rr_mean = np.log(grp["rr_mean"].astype(float).values)
        log_rr_low = np.log(grp["rr_low"].astype(float).values)
        log_rr_high = np.log(grp["rr_high"].astype(float).values)

        table[(risk, cause, age)] = {
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


def _evaluate_log_rr(
    table: RelativeRiskTable,
    risk: str,
    cause: str,
    age: str,
    intake: float,
    key: str = "log_rr_mean",
) -> float:
    """Interpolate log(RR) for given intake using linear interpolation in log-space."""
    data = table[(risk, cause, age)]
    exposures: npt.NDArray[np.floating] = data["exposures"]
    log_rr: npt.NDArray[np.floating] = data[key]

    if intake <= exposures[0]:
        return float(log_rr[0])
    if intake >= exposures[-1]:
        return float(log_rr[-1])

    return float(np.interp(intake, exposures, log_rr))


def _evaluate_log_rr_age_weighted(
    table: RelativeRiskTable,
    risk: str,
    cause: str,
    intake: float,
    age_weights: AgeWeights,
    cluster_id: int,
    key: str = "log_rr_mean",
) -> float:
    """Compute YLL-weighted effective log(RR) across age groups.

    Standard Comparative Risk Assessment (CRA) practice combines
    age-specific RR curves in log space:

        log RR_eff(x) = Σ_a w_a * log RR_a(x)

    which gives a weighted geometric mean of RRs. This is the convention
    used by IHME GBD and most peer-reviewed dietary-CRA work. The
    earlier arithmetic-mean variant gave log(Σ_a w_a RR_a), which
    over-estimates log RR_eff by Jensen's inequality (log is concave),
    inflating attributable burden.
    """
    log_rr_eff = 0.0
    for age in ADULT_AGES:
        w = age_weights.get((cluster_id, cause, age), 0.0)
        if w <= 0:
            continue
        log_rr_eff += w * _evaluate_log_rr(table, risk, cause, age, intake, key=key)
    return log_rr_eff


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
    max_per_capita: dict[str, float],
) -> dict[str, float]:
    """Per-risk intake-grid domain cap.

    The cap is the smallest domain that still covers every feasible solution:
    the larger of the empirical RR exposure range and the per-capita
    consumption cap (``max_per_capita``, enforced as ``e_nom_max`` on the
    food-group stores in build_model). Capping here rather than at a uniform
    generous limit avoids a long flat extrapolation plateau beyond the data,
    which would otherwise introduce a spurious concave kink for harmful
    (increasing) risk factors and force unnecessary integer branching.

    ``max_per_capita`` is assumed complete for all risk factors (enforced by
    ``validate_health_map``).
    """
    return {
        risk: max(float(max_exp), float(max_per_capita[risk]))
        for risk, max_exp in max_exposure_g_per_day.items()
    }


def _select_adaptive_knots(
    x: np.ndarray,
    curves: list[np.ndarray],
    rel_tol: float,
) -> np.ndarray:
    """Douglas-Peucker knot selection over a family of curves.

    Returns a boolean mask over ``x`` marking the minimal set of knots whose
    piecewise-linear interpolation reproduces every curve in ``curves`` to
    within ``rel_tol`` times that curve's amplitude (peak-to-peak log-RR
    range). A knot is kept whenever any curve's relative interpolation error
    exceeds the tolerance, so the shared per-risk grid stays accurate for every
    (cluster, cause) dose-response curve. Normalising per curve means a 5%
    target means "within 5% of each curve's own effect size", independent of
    whether the risk factor has a large or small log-RR.
    """
    n = len(x)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    if n <= 2 or not curves:
        return keep

    scales = [max(float(np.ptp(y)), 1e-12) for y in curves]
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg_x = x[a : b + 1]
        denom = x[b] - x[a]
        worst_rel = -1.0
        worst_k = -1
        for y, scale in zip(curves, scales):
            if denom > 0:
                interp = y[a] + (y[b] - y[a]) * (seg_x - x[a]) / denom
            else:
                interp = np.full_like(seg_x, y[a])
            rel_err = np.abs(y[a : b + 1] - interp) / scale
            k_local = int(np.argmax(rel_err))
            if rel_err[k_local] > worst_rel:
                worst_rel = float(rel_err[k_local])
                worst_k = a + k_local
        if worst_rel > rel_tol and worst_k not in (a, b):
            keep[worst_k] = True
            stack.append((a, worst_k))
            stack.append((worst_k, b))
    return keep


def _age_weighted_curve_on_grid(
    table: RelativeRiskTable,
    risk: str,
    cause: str,
    cluster_id: int,
    age_weights: AgeWeights,
    grid: np.ndarray,
    key: str,
) -> np.ndarray:
    """Vectorised YLL-weighted log(RR) over a whole intake grid.

    Equivalent to evaluating ``_evaluate_log_rr_age_weighted`` at every point
    of ``grid`` (``np.interp`` clamps to the curve endpoints outside the data
    range, matching the scalar helper), but done with one interpolation per age
    band instead of one per (age, grid point).
    """
    out = np.zeros(len(grid))
    for age in ADULT_AGES:
        w = age_weights.get((cluster_id, cause, age), 0.0)
        if w <= 0:
            continue
        data = table[(risk, cause, age)]
        out += w * np.interp(grid, data["exposures"], data[key])
    return out


def _compute_cluster_age_weights(
    combo: pd.DataFrame,
    cluster_to_countries: dict[int, list[str]],
    relevant_causes: list[str],
) -> AgeWeights:
    """Compute YLL-based age weights per (cluster, cause, age).

    For each (cluster, cause), the weight for age group a is:
        w_a = YLL_a / Σ_a' YLL_a'
    where YLL_a = death_rate_a x pop_a x life_exp_a (already computed in combo).

    Only adult ages (25+ in ADULT_AGES) are included.
    """
    age_weights: AgeWeights = {}

    for cluster_id, members in cluster_to_countries.items():
        cluster_combo = combo[combo["country"].isin(members)]
        adult_combo = cluster_combo[cluster_combo["age"].isin(ADULT_AGES)]

        if adult_combo.empty:
            # Uniform weights if no data
            for cause in relevant_causes:
                for age in ADULT_AGES:
                    age_weights[(cluster_id, cause, age)] = 1.0 / len(ADULT_AGES)
            continue

        yll_by_cause_age = adult_combo.groupby(["cause", "age"])["yll"].sum()

        for cause in relevant_causes:
            if cause in yll_by_cause_age.index.get_level_values("cause"):
                cause_yll = yll_by_cause_age.loc[cause]
                total = cause_yll.sum()
            else:
                cause_yll = pd.Series(dtype=float)
                total = 0.0

            for age in ADULT_AGES:
                if total > 0:
                    age_weights[(cluster_id, cause, age)] = (
                        float(cause_yll.get(age, 0.0)) / total
                    )
                else:
                    age_weights[(cluster_id, cause, age)] = 1.0 / len(ADULT_AGES)

    return age_weights


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
    age_weights: AgeWeights,
) -> tuple:
    """Process each health cluster to compute baseline metrics and intakes.

    Uses YLL-weighted effective RR across age groups for each cluster,
    accounting for the age structure of the disease burden.

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
            tmrel_intake = float(tmrel_g_per_day[risk])
            if not math.isfinite(tmrel_intake):
                tmrel_intake = 0.0
            tmrel_intake = max(0.0, min(tmrel_intake, max_exposure))

            causes = risk_to_causes[risk]
            for cause in causes:
                if (risk, cause, ADULT_AGES[0]) not in rr_lookup:
                    continue
                # Age-weighted effective log(RR) at TMREL and baseline
                log_rr_ref_totals[cause] += _evaluate_log_rr_age_weighted(
                    rr_lookup,
                    risk,
                    cause,
                    tmrel_intake,
                    age_weights,
                    cluster_id,
                )

                log_rr_baseline_totals[cause] += _evaluate_log_rr_age_weighted(
                    rr_lookup,
                    risk,
                    cause,
                    baseline_intake,
                    age_weights,
                    cluster_id,
                )

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
    rr_lookup: RelativeRiskTable,
    risk_to_causes: dict[str, list[str]],
    relevant_causes: list[str],
    log_rr_points: int,
    tmrel_g_per_day: dict[str, float],
    cluster_ids: list[int],
    age_weights: AgeWeights,
    breakpoint_rel_tol: float,
) -> tuple:
    """Generate piecewise-linear breakpoint tables for risks and causes.

    Produces cluster-specific risk breakpoints using YLL-weighted effective
    RR curves that account for the age structure of the disease burden in
    each health cluster.

    Intake grids (per risk factor, shared across clusters and causes):
        - The candidate knots are the native (smooth) RR exposure points -
          unioned across causes - plus TMREL, baseline intakes and the domain
          endpoints (0 and the per-risk cap).
        - `_select_adaptive_knots` keeps the smallest subset whose linear
          interpolation reproduces every (cluster, cause) curve - including the
          low/high uncertainty bounds - to within `breakpoint_rel_tol` of each
          curve's own amplitude. This trades a controlled accuracy loss for a
          much smaller Stage 1 MILP.
    Cause grids:
        - `log_rr_points` evenly spaced between aggregated min/max log(RR)
          implied by the risk grids above (global across all clusters).
    """
    risk_breakpoint_rows = []
    cause_log_min: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)
    cause_log_max: dict[str, float] = dict.fromkeys(relevant_causes, 0.0)
    keys = ("log_rr_mean", "log_rr_low", "log_rr_high")

    for risk in risk_factors:
        cap = float(intake_caps_g_per_day[risk])
        if cap <= 0:
            continue
        present_causes = [
            cause
            for cause in risk_to_causes[risk]
            if (risk, cause, ADULT_AGES[0]) in rr_lookup
        ]
        if not present_causes:
            continue

        # Candidate knots: the native (smooth) BoP exposure points, unioned
        # across causes, plus structural knots. No artificial grid is added -
        # the native points are already finely spaced.
        native = set()
        for cause in present_causes:
            native.update(
                float(x) for x in rr_lookup[(risk, cause, ADULT_AGES[0])]["exposures"]
            )
        lo = min(0.0, min(native))
        candidate_points = {0.0, lo, cap}
        candidate_points.update(x for x in native if lo <= x <= cap)
        for val in baseline_intake_registry[risk]:
            if lo <= float(val) <= cap:
                candidate_points.add(float(val))
        forced = {lo, cap}
        if risk in tmrel_g_per_day and lo <= tmrel_g_per_day[risk] <= cap:
            forced.add(float(tmrel_g_per_day[risk]))
        candidate_grid = np.array(sorted(candidate_points))

        # Evaluate mean/low/high curves for every (cluster, cause) on the
        # candidate grid once; reuse for both knot selection and output.
        evaluated: dict[tuple[int, str, str], np.ndarray] = {}
        curves = []
        for cause in present_causes:
            for cluster_id in cluster_ids:
                for key in keys:
                    vals = _age_weighted_curve_on_grid(
                        rr_lookup,
                        risk,
                        cause,
                        cluster_id,
                        age_weights,
                        candidate_grid,
                        key,
                    )
                    evaluated[(cluster_id, cause, key)] = vals
                    curves.append(vals)

        keep_mask = _select_adaptive_knots(candidate_grid, curves, breakpoint_rel_tol)
        for forced_x in forced:
            keep_mask[int(np.argmin(np.abs(candidate_grid - forced_x)))] = True
        grid = candidate_grid[keep_mask]

        for cause in present_causes:
            for cluster_id in cluster_ids:
                mean = evaluated[(cluster_id, cause, "log_rr_mean")][keep_mask]
                low = evaluated[(cluster_id, cause, "log_rr_low")][keep_mask]
                high = evaluated[(cluster_id, cause, "log_rr_high")][keep_mask]
                for i, intake in enumerate(grid):
                    risk_breakpoint_rows.append(
                        {
                            "health_cluster": cluster_id,
                            "risk_factor": risk,
                            "cause": cause,
                            "intake_g_per_day": float(intake),
                            "log_rr": float(mean[i]),
                            "log_rr_low": float(low[i]),
                            "log_rr_high": float(high[i]),
                        }
                    )
    # Aggregate per-cause log_rr bounds across risk factors and clusters.
    # For each cause, sum the most extreme log_rr (across mean / low /
    # high uncertainty bounds and across all clusters) over risk factors.
    if risk_breakpoint_rows:
        bp_df = pd.DataFrame(risk_breakpoint_rows)
        for cause in relevant_causes:
            cause_data = bp_df[bp_df["cause"] == cause]
            if cause_data.empty:
                continue
            for risk in risk_factors:
                risk_cause_data = cause_data[cause_data["risk_factor"] == risk]
                if risk_cause_data.empty:
                    continue
                # Across all clusters and intakes, find the most extreme log_rr
                all_log = pd.concat(
                    [
                        risk_cause_data["log_rr"],
                        risk_cause_data["log_rr_low"],
                        risk_cause_data["log_rr_high"],
                    ]
                )
                cause_log_min[cause] += float(all_log.min())
                cause_log_max[cause] += float(all_log.max())

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
    reference_year = int(snakemake.params["baseline_year"])
    log_rr_points = int(health_cfg["log_rr_points"])
    breakpoint_rel_tol = float(health_cfg["breakpoint_rel_tol"])
    intake_age_min = int(health_cfg["intake_age_min"])
    max_per_capita = {
        str(k): float(v) for k, v in snakemake.params["max_per_capita"].items()
    }

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

    # TMREL is canonical curated data (model basis), produced by
    # prepare_relative_risks; not derived from the curves here.
    tmrel_df = pd.read_csv(snakemake.input["tmrel"])
    tmrel_g_per_day = dict(
        zip(tmrel_df["risk_factor"], tmrel_df["tmrel_g_per_day"].astype(float))
    )
    missing_tmrel = [r for r in risk_factors if r not in tmrel_g_per_day]
    if missing_tmrel:
        raise ValueError(f"TMREL table missing risk factors: {missing_tmrel}")
    logger.info("Loaded TMREL for %d risks", len(tmrel_g_per_day))

    intake_caps_g_per_day = _build_intake_caps(max_exposure_g_per_day, max_per_capita)

    # Compute baseline health metrics
    combo = _compute_baseline_health_metrics(
        dr,
        pop_age,
        life_exp,
    )

    # Compute age-specific YLL weights per (cluster, cause, age)
    age_weights = _compute_cluster_age_weights(
        combo, cluster_to_countries, relevant_causes
    )
    logger.info(
        "Computed age-specific YLL weights for %d clusters x %d causes",
        len(cluster_to_countries),
        len(relevant_causes),
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
        age_weights,
    )

    # Generate piecewise-linear breakpoint tables
    cluster_ids = sorted(cluster_to_countries.keys())
    risk_breakpoints, cause_log_breakpoints = _generate_breakpoint_tables(
        risk_factors,
        intake_caps_g_per_day,
        baseline_intake_registry,
        rr_lookup,
        risk_to_causes,
        relevant_causes,
        log_rr_points,
        tmrel_g_per_day,
        cluster_ids,
        age_weights,
        breakpoint_rel_tol,
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


if __name__ == "__main__":
    # Configure logging
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)

    main()
