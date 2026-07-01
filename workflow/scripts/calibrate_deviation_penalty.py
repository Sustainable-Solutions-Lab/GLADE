# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Iterative calibration of L1 deviation-penalty coefficients.

Treats the map

    F : (log lambda_c1, ..., log lambda_cn) -> (log dev_c1, ..., log dev_cn)

for any non-empty subset of components ``{cropland, grassland, feed, diet}`` as a black-box
root-finder for ``F = (log target, ..., log target)``. The map is monotone
and near-affine in log-log coordinates, so a Broyden quasi-Newton iteration
converges in a handful of evaluations.

Each evaluation is a paired solve:
  1) baseline solve with ``enforce_baseline_diet=true`` to extract consumer
     values (piecewise utility blocks) under the matching L1 regime;
  2) main solve with the calibrated utility blocks active and the listed
     components' penalties enabled.

Convergence target: ``|log(d / target)|_inf < tolerance`` across all
components in the calibration subset.
"""

import logging
from pathlib import Path
import time

import numpy as np
import pandas as pd
import yaml

from workflow.scripts.analysis.extract_baseline_deviation import (
    extract_baseline_deviation,
)
from workflow.scripts.calibrate_food_utility_blocks import (
    _calibrate_blocks,
    _load_baseline_food_consumption,
    drop_zero_baseline_pairs,
)
from workflow.scripts.extract_consumer_values import extract_consumer_values
from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.solve_model.core import _ShadowPriceLogFilter, run_solve
from workflow.scripts.solve_namespace import (
    build_namespace,
    build_scenario_entry,
    default_path_roots,
)

logger = logging.getLogger(__name__)

# Order in which components appear in vectors and tables. The actual subset
# being calibrated is a non-empty selection from this list.
COMPONENT_ORDER = ("cropland", "grassland", "feed", "diet")


def _make_overrides(
    lambdas: dict[str, float],
    components: list[str],
    *,
    enforce_baseline: bool,
) -> dict:
    """Build a deviation_penalty override block for one iteration.

    ``lambdas`` maps each component in ``components`` to its current L1
    cost iterate. Components not in ``components`` are forced off so the
    calibration measures the deviations driven only by the listed knobs.

    The **baseline** solve always runs at ghg_price=0 / value_per_yll=0
    so consumer values are derived from raw revealed preferences
    (independent of any policy regime under investigation). The **main**
    solve inherits emissions / health pricing from ``base_config``; set
    them at the top level of the calibration config to pin the regime.
    """
    cropland_on = "cropland" in components
    grassland_on = "grassland" in components
    land_block: dict = {
        "enabled": cropland_on or grassland_on,
        "crops": {"enabled": cropland_on},
        "grassland": {"enabled": grassland_on},
    }
    feed_block: dict = {"enabled": "feed" in components}
    diet_block: dict = {"enabled": "diet" in components}
    if cropland_on:
        land_block["crops"]["l1_cost"] = float(lambdas["cropland"])
    if grassland_on:
        land_block["grassland"]["l1_cost"] = float(lambdas["grassland"])
    if "feed" in components:
        feed_block["l1_cost"] = float(lambdas["feed"])
    if "diet" in components:
        diet_block["l1_cost"] = float(lambdas["diet"])
    overrides: dict = {
        "validation": {
            "enforce_baseline_diet": enforce_baseline,
        },
        "deviation_penalty": {
            "enabled": True,
            "penalty_mode": "l1",
            "deviation_type": "absolute",
            "land": land_block,
            "feed": feed_block,
            "diet": diet_block,
        },
        "food_utility_piecewise": {"enabled": not enforce_baseline},
    }
    if enforce_baseline:
        overrides["emissions"] = {"ghg_price": 0}
        overrides["health"] = {"value_per_yll": 0}
    return overrides


def _write_utility_blocks(n_baseline, base_config: dict, blocks_path: Path) -> None:
    """Recreate the workflow/rules/consumer_values.smk outputs in-memory."""
    values_df = extract_consumer_values(n_baseline)
    baseline_df = _load_baseline_food_consumption(n_baseline)
    merged = baseline_df.merge(
        values_df[["food", "country", "value_bnusd_per_mt"]],
        on=["food", "country"],
        how="inner",
    )
    if merged.empty:
        raise ValueError(
            "No overlapping (food, country) pairs between baseline and values"
        )
    merged = drop_zero_baseline_pairs(merged)
    if merged.empty:
        raise ValueError(
            "All (food, country) pairs have negligible baseline consumption"
        )
    utility_cfg = base_config["food_utility_piecewise"]
    blocks_df = _calibrate_blocks(
        merged,
        n_blocks=int(utility_cfg["n_blocks"]),
        decline_factor=float(utility_cfg["decline_factor"]),
        total_width_multiplier=float(utility_cfg["total_width_multiplier"]),
    )
    blocks_path.parent.mkdir(parents=True, exist_ok=True)
    blocks_df.to_csv(blocks_path, index=False)


def _deviation_pcts(n_main, components: list[str]) -> dict[str, float]:
    """Return per-component absolute deviation as a percentage of baseline."""
    df = extract_baseline_deviation(n_main).set_index("component")
    pcts: dict[str, float] = {}
    if "cropland" in components:
        crop_bl = df.loc["crop_area", "baseline_total"]
        crop_dev = df.loc["crop_area", "abs_deviation"]
        if crop_bl <= 0:
            raise ValueError(
                "Cropland baseline_total is non-positive; cannot calibrate"
            )
        pcts["cropland"] = float(100 * crop_dev / crop_bl)
    if "grassland" in components:
        pasture_bl = df.loc["pasture_area", "baseline_total"]
        pasture_dev = df.loc["pasture_area", "abs_deviation"]
        if pasture_bl <= 0:
            raise ValueError(
                "Grassland (pasture) baseline_total is non-positive; cannot calibrate"
            )
        pcts["grassland"] = float(100 * pasture_dev / pasture_bl)
    if "feed" in components:
        feed_bl = df.loc["animal_feed_use", "baseline_total"]
        feed_dev = df.loc["animal_feed_use", "abs_deviation"]
        if feed_bl <= 0:
            raise ValueError("Feed baseline_total is non-positive; cannot calibrate")
        pcts["feed"] = float(100 * feed_dev / feed_bl)
    if "diet" in components:
        diet_bl = df.loc["food_consumption", "baseline_total"]
        diet_dev = df.loc["food_consumption", "abs_deviation"]
        if not np.isfinite(diet_bl) or diet_bl <= 0:
            raise ValueError(
                "food_consumption baseline_total is non-positive or missing; "
                "diet calibration requires baseline_consumption_mt to be "
                "stamped on food_consumption links (the matched_baseline must "
                "be computed during the main solve)."
            )
        pcts["diet"] = float(100 * diet_dev / diet_bl)
    return pcts


def _evaluate(
    lambdas: dict[str, float],
    *,
    components: list[str],
    iter_id: int,
    base_config: dict,
    name: str,
    path_roots: dict[str, str],
) -> tuple[dict[str, float], dict]:
    """Run baseline + main solve at the given iterate; return deviation pcts."""
    baseline_scen = f"_cal_baseline_iter{iter_id:02d}"
    main_scen = f"_cal_main_iter{iter_id:02d}"

    overrides_base = _make_overrides(lambdas, components, enforce_baseline=True)
    overrides_main = {
        **_make_overrides(lambdas, components, enforce_baseline=False),
        "consumer_values": {"baseline_scenario": baseline_scen},
        "macronutrients": {"cal": {"equal_to_baseline": True}},
    }
    scenario_defs = {baseline_scen: overrides_base, main_scen: overrides_main}

    timings: dict[str, float] = {}
    lambda_str = ", ".join(f"{c}={lambdas[c]:.6g}" for c in components)

    # --- Baseline solve ---
    logger.info("[iter %d] baseline solve: %s", iter_id, lambda_str)
    entry_b = build_scenario_entry(
        base_config, baseline_scen, name, path_roots, False, scenario_defs
    )
    Path(entry_b["log"]).parent.mkdir(parents=True, exist_ok=True)
    smk_b = build_namespace(entry_b)
    t0 = time.time()
    n_baseline = run_solve(
        smk_b,
        logger,
        skip_post_processing=True,
        skip_assign_duals=True,
        accept_time_limit=True,
    )
    timings["baseline_s"] = time.time() - t0
    if n_baseline is None:
        raise RuntimeError(f"[iter {iter_id}] baseline solve failed at {lambda_str}")

    # --- Calibrate utility blocks (consumer_values workflow inline) ---
    entry_m_preview = build_scenario_entry(
        base_config, main_scen, name, path_roots, False, scenario_defs
    )
    blocks_path = Path(entry_m_preview["inputs"]["food_utility_piecewise"])
    _write_utility_blocks(n_baseline, base_config, blocks_path)
    del n_baseline

    # --- Main solve ---
    logger.info("[iter %d] main solve", iter_id)
    smk_m = build_namespace(entry_m_preview)
    t0 = time.time()
    n_main = run_solve(
        smk_m,
        logger,
        skip_post_processing=True,
        skip_assign_duals=True,
        accept_time_limit=True,
    )
    timings["main_s"] = time.time() - t0
    if n_main is None:
        raise RuntimeError(f"[iter {iter_id}] main solve failed at {lambda_str}")

    pcts = _deviation_pcts(n_main, components)
    pct_str = "  ".join(f"{c}_dev={pcts[c]:.3f}%" for c in components)
    logger.info(
        "[iter %d] %s  (baseline_s=%.1f  main_s=%.1f)",
        iter_id,
        pct_str,
        timings["baseline_s"],
        timings["main_s"],
    )
    return pcts, timings


def broyden_iterate(
    evaluate_fn,
    x0: np.ndarray,
    J0: np.ndarray,
    target: float,
    tol: float,
    max_iter: int,
    trust_log: float,
    components: list[str],
) -> tuple[np.ndarray, list[dict]]:
    """Broyden's "good" method in log coords, generalised to N components.

    ``x[i] = log lambda_i``;
    ``r(x)[i] = log(d_i / target)`` for each component i in ``components``.
    """
    x = np.asarray(x0, dtype=float).copy()
    J = np.asarray(J0, dtype=float).copy()
    trace: list[dict] = []

    def _record(iter_id: int, x_vec: np.ndarray, d_vec: np.ndarray, r_vec: np.ndarray):
        row: dict = {"iter": iter_id, "resid_inf": float(np.max(np.abs(r_vec)))}
        for i, c in enumerate(components):
            row[f"lambda_{c}"] = float(np.exp(x_vec[i]))
            row[f"{c}_pct"] = float(d_vec[i])
        trace.append(row)

    pcts = evaluate_fn(
        {c: float(np.exp(x[i])) for i, c in enumerate(components)}, iter_id=0
    )
    d = np.array([pcts[c] for c in components])
    r = np.log(d / target)
    _record(0, x, d, r)
    logger.info(
        "iter 0: lambdas=(%s)  d=(%s)  |r|_inf=%.4f",
        ", ".join(f"{np.exp(xi):.5g}" for xi in x),
        ", ".join(f"{di:.3f}%" for di in d),
        float(np.max(np.abs(r))),
    )

    for k in range(1, max_iter + 1):
        if np.max(np.abs(r)) < tol:
            return x, trace

        try:
            dx = -np.linalg.solve(J, r)
        except np.linalg.LinAlgError:
            logger.warning("Singular Jacobian; falling back to -diag-scaled r")
            dx = -r / np.diag(J).clip(min=0.1, max=None)

        max_dx = float(np.max(np.abs(dx)))
        if max_dx > trust_log:
            dx = dx * (trust_log / max_dx)

        x_new = x + dx
        pcts = evaluate_fn(
            {c: float(np.exp(x_new[i])) for i, c in enumerate(components)},
            iter_id=k,
        )
        d = np.array([pcts[c] for c in components])
        r_new = np.log(d / target)
        _record(k, x_new, d, r_new)
        logger.info(
            "iter %d: lambdas=(%s)  d=(%s)  |r|_inf=%.4f",
            k,
            ", ".join(f"{np.exp(xi):.5g}" for xi in x_new),
            ", ".join(f"{di:.3f}%" for di in d),
            float(np.max(np.abs(r_new))),
        )

        s = dx
        y = r_new - r
        denom = float(s @ s)
        if denom > 1e-12:
            J = J + np.outer(y - J @ s, s) / denom

        x = x_new
        r = r_new

    # Out of iterations: return the best iterate seen rather than the last.
    # With a discontinuous deviation response (LP basis switches near the
    # target), the final Broyden step can land worse than an earlier iterate.
    best = min(trace, key=lambda row: row["resid_inf"])
    if best["iter"] != trace[-1]["iter"]:
        logger.info(
            "Not converged; returning best iterate %d (|r|_inf=%.4f) "
            "instead of last (%.4f)",
            best["iter"],
            best["resid_inf"],
            trace[-1]["resid_inf"],
        )
    x_best = np.array([np.log(best[f"lambda_{c}"]) for c in components])
    return x_best, trace


def main() -> None:
    smk = snakemake  # type: ignore[name-defined]

    components: list[str] = list(smk.params.components)
    if not components:
        raise ValueError("deviation_penalty.calibration.components must be non-empty")
    unknown = [c for c in components if c not in COMPONENT_ORDER]
    if unknown:
        raise ValueError(
            f"Unknown calibration components {unknown}; "
            f"expected subset of {list(COMPONENT_ORDER)}"
        )
    # Canonical order so the output is reproducible regardless of input order.
    components = [c for c in COMPONENT_ORDER if c in components]

    target_pct = float(smk.params.target_pct)
    name = smk.params.name
    seeds: dict[str, float] = {c: float(smk.params.seeds[c]) for c in components}
    tol = float(smk.params.tolerance)
    max_iter = int(smk.params.max_iter)
    trust_log = float(smk.params.trust_region_log)

    # Warm-start from a previously calibrated YAML when available. The path
    # is passed as a param (not an input) to avoid a Snakemake self-loop on
    # calibrated_yaml. The script loads it iff the file exists on disk.
    prev_yaml = getattr(smk.params, "previous_yaml", None)
    if prev_yaml and Path(prev_yaml).exists():
        with open(prev_yaml) as f:
            prev = yaml.safe_load(f)
        prev_l1 = prev.get("l1_costs", {})
        warm = {c: prev_l1[c] for c in components if c in prev_l1}
        if warm:
            seeds.update({c: float(v) for c, v in warm.items()})
            logger.info(
                "Warm-starting from previous calibration: %s",
                ", ".join(f"{c}={seeds[c]:.6g}" for c in components),
            )

    base_config = dict(smk.config)
    base_config.pop("scenarios", None)
    path_roots = default_path_roots(base_config)

    x0 = np.array([np.log(seeds[c]) for c in components])
    # Diagonal Jacobian prior: in log-log, d log d_i / d log lambda_i ~= -1
    # if d ~ 1 / lambda.
    J0 = -np.eye(len(components))

    def evaluate_fn(lambdas: dict[str, float], *, iter_id: int) -> dict[str, float]:
        pcts, _ = _evaluate(
            lambdas,
            components=components,
            iter_id=iter_id,
            base_config=base_config,
            name=name,
            path_roots=path_roots,
        )
        return pcts

    x_final, trace = broyden_iterate(
        evaluate_fn,
        x0,
        J0,
        target=target_pct,
        tol=tol,
        max_iter=max_iter,
        trust_log=trust_log,
        components=components,
    )
    l1_costs = {c: float(np.exp(x_final[i])) for i, c in enumerate(components)}
    # The residual of the returned iterate: the converged row on success,
    # otherwise the best iterate that broyden_iterate falls back to.
    final_resid = min(row["resid_inf"] for row in trace)

    converged = final_resid < tol
    if not converged:
        logger.warning(
            "Did not converge within %d iterations: |r|_inf=%.4f (tol=%.4f)",
            max_iter,
            final_resid,
            tol,
        )

    trace_path = Path(smk.output.trace)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trace).to_csv(trace_path, index=False)
    logger.info("Wrote iteration trace to %s", trace_path)

    out_path = Path(smk.output.calibrated_yaml)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek\n"
        "#\n"
        "# SPDX-License-Identifier: CC-BY-4.0\n"
        "#\n"
        "# Auto-generated by workflow/scripts/calibrate_deviation_penalty.py.\n"
        "# Consumed at solve time when deviation_penalty.<component>.l1_cost\n"
        '# is the sentinel string "calibrated".\n'
        "# Do not edit by hand -- run ``tools/calibrate stability`` to regenerate.\n"
    )
    body = yaml.safe_dump(
        {
            "target_deviation_pct": target_pct,
            "penalty_mode": "l1",
            "deviation_type": "absolute",
            "components": components,
            "l1_costs": l1_costs,
            "iterations": len(trace) - 1,
            "converged": bool(converged),
            "final_residual_log_inf": float(final_resid),
        },
        sort_keys=False,
    )
    out_path.write_text(header + body)
    # Side copy for warm-starting the next calibration: Snakemake deletes the
    # calibrated_yaml output before rerunning this rule, so the warm-start
    # seed must live outside the rule's outputs.
    prev_path = Path(smk.params.previous_yaml)
    prev_path.parent.mkdir(parents=True, exist_ok=True)
    prev_path.write_text(header + body)
    logger.info(
        "Wrote calibrated L1 costs to %s (%s, iters=%d, converged=%s)",
        out_path,
        ", ".join(f"{c}={l1_costs[c]:.6g}" for c in components),
        len(trace) - 1,
        converged,
    )


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())
    main()
