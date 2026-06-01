# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build dietary relative-risk curves from GBD 2023 Burden-of-Proof data.

Reads the raw age-aggregated Burden-of-Proof (BoP) curves
(``data/downloads/burden_of_proof/bop_rr_curves.csv``), the curated
age-attenuation table (``rr_age_attenuation.csv``) and the curated TMREL table
(``rr_tmrel.csv``), then for each ``(risk_factor, cause)``:

1. converts the exposure axis from the GBD intake basis to the model's
   consumption basis (``diet.source_basis`` + ``weight_conversion``);
2. clips the curve at the TMREL (theoretical minimum risk exposure level), so
   intake beyond the TMREL yields no further benefit for protective risks (and
   below it none for harmful risks). The TMREL is the canonical reference; it is
   also written to ``tmrel.csv`` in model basis;
3. expands the single all-ages curve into the 15 adult age groups using the
   curated multiplicative log-RR attenuation: ``RR_age(x) = exp(beta(age) *
   log RR_clipped(x))``.

Risks listed in ``config.health.alternative_rr`` replace the BoP dose-response
with a log-linear curve from a literature CSV, age-corrected with the same
curated ``beta`` table.

Outputs (tidy long):
    relative_risks.csv: risk_factor, cause, age, exposure_g_per_day,
                        rr_mean, rr_low, rr_high
    tmrel.csv:          risk_factor, tmrel_g_per_day  (model basis)
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.diet.basis import (
    build_group_basis,
    conversion_factor,
    load_food_basis,
)
from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)

# The 15 adult GBD age buckets the health pipeline expects.
ADULT_AGE_LABELS: list[str] = [
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

_CURVE_COLS = [
    "risk_factor",
    "cause",
    "exposure_g_per_day",
    "rr_mean",
    "rr_low",
    "rr_high",
]


def _basis_factor_by_risk(
    source_basis: dict, weight_conversion: dict, food_basis_path, food_groups_path
) -> dict[str, float]:
    """Per-risk multiplier mapping the GBD intake basis onto the model basis.

    Mirrors the diet basis pipeline: each risk's GBD basis (``source_basis.gbd``)
    is compared to the matching food group's basis (``food_basis.csv`` via
    ``food_groups.csv``); when they differ the matching ``weight_conversion``
    factor is applied to the RR curve x-axis (and the TMREL).
    """
    food_basis = load_food_basis(food_basis_path)
    food_to_group = pd.read_csv(food_groups_path).set_index("food")["group"].to_dict()
    group_basis = build_group_basis(food_basis, food_to_group)
    gbd_basis = {str(g): str(b) for g, b in source_basis.get("gbd", {}).items()}

    factors: dict[str, float] = {}
    for risk_id, src in gbd_basis.items():
        tgt = group_basis.get(risk_id)
        if tgt is None or src == tgt:
            continue
        factors[risk_id] = conversion_factor(src, tgt, risk_id, weight_conversion)
        logger.info(
            "GBD->model basis factor %.3f for %s (%s -> %s)",
            factors[risk_id],
            risk_id,
            src,
            tgt,
        )
    return factors


def _parse_per_unit(per_unit: str) -> float:
    """Parse a per_unit string like '100 g/day' into the numeric g/day value."""
    parts = str(per_unit).strip().split()
    if len(parts) < 2:
        raise ValueError(f"Cannot parse per_unit: '{per_unit}'")
    return float(parts[0])


def _override_all_ages(
    csv_path: str, risk: str, causes: list[str], grid: list[float]
) -> pd.DataFrame:
    """Build all-ages log-linear curves ``RR(x) = rr^(x / per_unit)`` from a CSV."""
    alt = pd.read_csv(csv_path)
    required = {"outcome", "rr_central", "rr_lower_95ci", "rr_upper_95ci", "per_unit"}
    missing = required - set(alt.columns)
    if missing:
        raise ValueError(
            f"Alternative RR CSV {csv_path} missing columns: {sorted(missing)}"
        )

    rows: list[dict] = []
    found: set[str] = set()
    for _, r in alt.iterrows():
        cause = str(r["outcome"])
        if cause not in causes:
            continue
        found.add(cause)
        per_unit = _parse_per_unit(r["per_unit"])
        central, low, high = (
            float(r["rr_central"]),
            float(r["rr_lower_95ci"]),
            float(r["rr_upper_95ci"]),
        )
        for x in grid:
            ratio = x / per_unit
            rows.append(
                {
                    "risk_factor": risk,
                    "cause": cause,
                    "exposure_g_per_day": x,
                    "rr_mean": central**ratio,
                    "rr_low": low**ratio,
                    "rr_high": high**ratio,
                }
            )
    missing_causes = set(causes) - found
    if missing_causes:
        raise ValueError(
            f"Alternative RR CSV {csv_path} for '{risk}' missing causes: {sorted(missing_causes)}"
        )
    logger.info("Built log-linear override curves for %s (%d causes)", risk, len(found))
    return pd.DataFrame(rows, columns=_CURVE_COLS)


def _ensure_knot(g: pd.DataFrame, x0: float) -> pd.DataFrame:
    """Insert exposure knot x0 (log-linear interpolation) if not already present."""
    xs = g["exposure_g_per_day"].to_numpy(float)
    if np.any(np.isclose(xs, x0)):
        return g
    row = {
        "risk_factor": g["risk_factor"].iloc[0],
        "cause": g["cause"].iloc[0],
        "exposure_g_per_day": float(x0),
    }
    for col in ("rr_mean", "rr_low", "rr_high"):
        row[col] = float(np.exp(np.interp(x0, xs, np.log(g[col].to_numpy(float)))))
    return (
        pd.concat([g, pd.DataFrame([row])], ignore_index=True)
        .sort_values("exposure_g_per_day")
        .reset_index(drop=True)
    )


def _clip_at_tmrel(g: pd.DataFrame, tmrel: float, risk_type: str) -> pd.DataFrame:
    """Truncate the curve at the TMREL so the flat plateau lies on its benefit side.

    Protective risks: keep exposures <= TMREL (downstream flat-extrapolation gives
    no further benefit above TMREL). Harmful risks: keep exposures >= TMREL.
    """
    g = g.sort_values("exposure_g_per_day").reset_index(drop=True)
    xs = g["exposure_g_per_day"].to_numpy(float)
    risk, cause = g["risk_factor"].iloc[0], g["cause"].iloc[0]

    if risk_type == "protective":
        if tmrel <= xs[0]:
            raise ValueError(
                f"Protective TMREL {tmrel} <= min exposure for {risk}->{cause}"
            )
        if tmrel < xs[-1]:
            g = _ensure_knot(g, tmrel)
            g = g[g["exposure_g_per_day"] <= tmrel + 1e-9]
    elif risk_type == "harmful":
        if tmrel >= xs[-1]:
            raise ValueError(
                f"Harmful TMREL {tmrel} >= max exposure for {risk}->{cause}"
            )
        if tmrel > xs[0]:
            g = _ensure_knot(g, tmrel)
            g = g[g["exposure_g_per_day"] >= tmrel - 1e-9]
    else:
        raise ValueError(f"Unknown risk_type {risk_type!r} for {risk}")
    return g.reset_index(drop=True)


def _age_expand(
    g: pd.DataFrame,
    risk: str,
    cause: str,
    beta_lookup: dict[tuple[str, str, str], float],
) -> pd.DataFrame:
    """Expand an all-ages curve to 15 ages: RR_age = exp(beta(age) * log RR)."""
    log_mean = np.log(g["rr_mean"].to_numpy(float))
    log_low = np.log(g["rr_low"].to_numpy(float))
    log_high = np.log(g["rr_high"].to_numpy(float))
    x = g["exposure_g_per_day"].to_numpy(float)

    frames = []
    for age in ADULT_AGE_LABELS:
        beta = beta_lookup[(risk, cause, age)]
        frames.append(
            pd.DataFrame(
                {
                    "risk_factor": risk,
                    "cause": cause,
                    "age": age,
                    "exposure_g_per_day": x,
                    "rr_mean": np.exp(beta * log_mean),
                    "rr_low": np.exp(beta * log_low),
                    "rr_high": np.exp(beta * log_high),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    snakemake = globals().get("snakemake")  # type: ignore
    if snakemake is None:
        raise RuntimeError("This script must run via Snakemake")

    risk_factors: list[str] = list(snakemake.params["risk_factors"])
    risk_cause_map = {
        str(r): list(cs) for r, cs in dict(snakemake.params["risk_cause_map"]).items()
    }
    alternative_rr = {
        str(k): v for k, v in dict(snakemake.params["alternative_rr"]).items() if v
    }
    source_basis = {
        src: {str(g): str(b) for g, b in groups.items()}
        for src, groups in dict(snakemake.params["source_basis"]).items()
    }
    weight_conversion = {
        str(t): {str(k): float(v) for k, v in entries.items()}
        for t, entries in dict(snakemake.params["weight_conversion"]).items()
    }

    basis_factor = _basis_factor_by_risk(
        source_basis,
        weight_conversion,
        snakemake.input["food_basis"],
        snakemake.input["food_groups"],
    )

    # Raw all-ages BoP curves, exposure converted to model basis.
    bop = pd.read_csv(snakemake.input["bop_curves"])
    bop["exposure_g_per_day"] = bop["exposure_g_per_day"] * bop["risk_factor"].map(
        lambda r: basis_factor.get(r, 1.0)
    )

    # Curated age-attenuation and TMREL tables.
    beta_df = pd.read_csv(snakemake.input["beta"])
    beta_lookup = {
        (r, c, a): float(b)
        for r, c, a, b in beta_df[["risk_factor", "cause", "age", "beta"]].itertuples(
            index=False
        )
    }
    tmrel_df = pd.read_csv(snakemake.input["tmrel"]).set_index("risk_factor")
    tmrel_model: dict[str, float] = {}
    risk_type: dict[str, str] = {}
    for risk in risk_factors:
        row = tmrel_df.loc[risk]
        f = basis_factor.get(risk, 1.0)
        tmrel_model[risk] = (
            0.5 * (float(row["tmrel_low"]) + float(row["tmrel_high"])) * f
        )
        risk_type[risk] = str(row["risk_type"])

    # Build the all-ages curve per risk (BoP, or literature override).
    all_ages_frames = []
    for risk in risk_factors:
        causes = risk_cause_map[risk]
        if risk in alternative_rr:
            grid = sorted(
                bop.loc[bop["risk_factor"] == risk, "exposure_g_per_day"].unique()
            )
            if not grid:
                raise ValueError(
                    f"No BoP exposure grid available for override risk '{risk}'"
                )
            all_ages_frames.append(
                _override_all_ages(alternative_rr[risk], risk, causes, grid)
            )
        else:
            sub = bop[(bop["risk_factor"] == risk) & (bop["cause"].isin(causes))]
            all_ages_frames.append(sub[_CURVE_COLS])
    all_ages = pd.concat(all_ages_frames, ignore_index=True)

    # Validate every required (risk, cause) pair is present before processing.
    required_pairs = {(r, c) for r in risk_factors for c in risk_cause_map[r]}
    present_pairs = set(
        map(tuple, all_ages[["risk_factor", "cause"]].drop_duplicates().to_numpy())
    )
    missing = sorted(required_pairs - present_pairs)
    if missing:
        raise ValueError(
            "Relative risk curves missing required risk-cause pairs: "
            + ", ".join(f"{r}:{c}" for r, c in missing)
        )

    # Clip at TMREL, then age-expand.
    out_frames = []
    for risk in risk_factors:
        for cause in risk_cause_map[risk]:
            g = all_ages[
                (all_ages["risk_factor"] == risk) & (all_ages["cause"] == cause)
            ].sort_values("exposure_g_per_day")
            g = _clip_at_tmrel(g, tmrel_model[risk], risk_type[risk])
            out_frames.append(_age_expand(g, risk, cause, beta_lookup))

    relative_risks = (
        pd.concat(out_frames, ignore_index=True)
        .sort_values(["risk_factor", "cause", "age", "exposure_g_per_day"])
        .reset_index(drop=True)
    )

    out_path = Path(snakemake.output["relative_risks"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    relative_risks.to_csv(out_path, index=False)

    tmrel_out = pd.DataFrame(
        [{"risk_factor": r, "tmrel_g_per_day": tmrel_model[r]} for r in risk_factors]
    ).sort_values("risk_factor")
    tmrel_out.to_csv(snakemake.output["tmrel"], index=False)

    logger.info(
        "Wrote %d RR rows and %d TMREL values to %s",
        len(relative_risks),
        len(tmrel_out),
        out_path.parent,
    )


if __name__ == "__main__":
    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
