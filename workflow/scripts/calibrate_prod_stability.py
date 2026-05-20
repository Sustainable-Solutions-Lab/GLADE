# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Calibrate L1 production-stability penalty costs.

Treats the map

    F : (lambda_c, lambda_a) -> (land_dev_pct, feed_dev_pct)

as a black-box 2D root-finder for ``F = (t, t)``. The map is monotone and
near-affine in log-log coordinates, so a Broyden quasi-Newton iteration
converges in a handful of evaluations.

Each evaluation is a paired solve:
  1) baseline solve with ``enforce_baseline_diet=true`` to extract consumer
     values (piecewise utility blocks) under the matching L1 regime;
  2) main solve with the calibrated utility blocks active.

Convergence target: ``|log(d / target)|_inf < tolerance`` on both deviations.
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


def _stability_overrides(
    lambda_c: float, lambda_a: float, *, enforce_baseline: bool
) -> dict:
    return {
        "validation": {
            "enforce_baseline_diet": enforce_baseline,
            "production_stability": {
                "enabled": True,
                "penalty_mode": "l1",
                "deviation_type": "absolute",
                "land_l1_cost": float(lambda_c),
                "animal_feed_l1_cost": float(lambda_a),
            },
        },
        # Pin the calibration regime: GHG/health pricing off, piecewise
        # utility on only for the main solve.
        "emissions": {"ghg_price": 0},
        "health": {"value_per_yll": 0},
        "food_utility_piecewise": {"enabled": not enforce_baseline},
    }


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
    # Keep the inline calibration consistent with the standalone
    # calibrate_food_utility_blocks.py path: drop (food, country) pairs
    # with negligible baseline consumption so _calibrate_blocks doesn't
    # generate microscopic width-clamped blocks.
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


def _deviation_pcts(n_main) -> tuple[float, float]:
    df = extract_baseline_deviation(n_main).set_index("component")
    land_bl = (
        df.loc["crop_area", "baseline_total"] + df.loc["pasture_area", "baseline_total"]
    )
    land_dev = (
        df.loc["crop_area", "abs_deviation"] + df.loc["pasture_area", "abs_deviation"]
    )
    feed_bl = df.loc["animal_feed_use", "baseline_total"]
    feed_dev = df.loc["animal_feed_use", "abs_deviation"]
    if land_bl <= 0 or feed_bl <= 0:
        raise ValueError(
            f"Calibration scenario has zero baseline totals "
            f"(land={land_bl}, feed={feed_bl}); cannot compute deviation pct"
        )
    return float(100 * land_dev / land_bl), float(100 * feed_dev / feed_bl)


def _evaluate(
    lambda_c: float,
    lambda_a: float,
    *,
    iter_id: int,
    base_config: dict,
    name: str,
    path_roots: dict[str, str],
) -> tuple[float, float, dict]:
    """Run baseline + main solve at (λ_c, λ_a); return deviation percentages."""
    baseline_scen = f"_cal_baseline_iter{iter_id:02d}"
    main_scen = f"_cal_main_iter{iter_id:02d}"

    scenario_defs = {
        baseline_scen: _stability_overrides(lambda_c, lambda_a, enforce_baseline=True),
        main_scen: {
            **_stability_overrides(lambda_c, lambda_a, enforce_baseline=False),
            "consumer_values": {"baseline_scenario": baseline_scen},
            "macronutrients": {"cal": {"equal_to_baseline": True}},
        },
    }

    timings: dict[str, float] = {}

    # --- Baseline solve ---
    logger.info(
        "[iter %d] baseline solve: lambda_c=%.6g lambda_a=%.6g",
        iter_id,
        lambda_c,
        lambda_a,
    )
    entry_b = build_scenario_entry(
        base_config, baseline_scen, name, path_roots, False, scenario_defs
    )
    Path(entry_b["log"]).parent.mkdir(parents=True, exist_ok=True)
    smk_b = build_namespace(entry_b)
    t0 = time.time()
    n_baseline = run_solve(
        smk_b, logger, skip_post_processing=True, skip_assign_duals=True
    )
    timings["baseline_s"] = time.time() - t0
    if n_baseline is None:
        raise RuntimeError(
            f"[iter {iter_id}] baseline solve failed at "
            f"lambda_c={lambda_c:.6g}, lambda_a={lambda_a:.6g}"
        )

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
    n_main = run_solve(smk_m, logger, skip_post_processing=True, skip_assign_duals=True)
    timings["main_s"] = time.time() - t0
    if n_main is None:
        raise RuntimeError(
            f"[iter {iter_id}] main solve failed at "
            f"lambda_c={lambda_c:.6g}, lambda_a={lambda_a:.6g}"
        )

    land_pct, feed_pct = _deviation_pcts(n_main)
    logger.info(
        "[iter %d] land_dev=%.3f%%  feed_dev=%.3f%%  (baseline_s=%.1f  main_s=%.1f)",
        iter_id,
        land_pct,
        feed_pct,
        timings["baseline_s"],
        timings["main_s"],
    )
    return land_pct, feed_pct, timings


def broyden_iterate(
    evaluate_fn,
    x0: np.ndarray,
    J0: np.ndarray,
    target: float,
    tol: float,
    max_iter: int,
    trust_log: float,
) -> tuple[np.ndarray, list[dict]]:
    """Broyden's "good" method in log coords.

    ``x = (log lambda_c, log lambda_a)``;
    ``r(x) = (log(d_c/target), log(d_a/target))``.
    """
    x = np.asarray(x0, dtype=float).copy()
    J = np.asarray(J0, dtype=float).copy()
    trace: list[dict] = []

    d = np.array(evaluate_fn(*np.exp(x), iter_id=0)[:2])
    r = np.log(d / target)
    trace.append(
        {
            "iter": 0,
            "lambda_c": float(np.exp(x[0])),
            "lambda_a": float(np.exp(x[1])),
            "land_pct": float(d[0]),
            "feed_pct": float(d[1]),
            "resid_inf": float(np.max(np.abs(r))),
        }
    )
    logger.info(
        "iter 0: lambda=(%.5g, %.5g)  d=(%.3f%%, %.3f%%)  |r|_inf=%.4f",
        np.exp(x[0]),
        np.exp(x[1]),
        d[0],
        d[1],
        np.max(np.abs(r)),
    )

    for k in range(1, max_iter + 1):
        if np.max(np.abs(r)) < tol:
            return x, trace

        try:
            dx = -np.linalg.solve(J, r)
        except np.linalg.LinAlgError:
            logger.warning("Singular Jacobian; falling back to -diag-scaled r")
            dx = -r / np.diag(J).clip(min=0.1, max=None)

        # Trust region: bound |Δx|_inf in log-space.
        max_dx = float(np.max(np.abs(dx)))
        if max_dx > trust_log:
            dx = dx * (trust_log / max_dx)

        x_new = x + dx
        d = np.array(evaluate_fn(*np.exp(x_new), iter_id=k)[:2])
        r_new = np.log(d / target)
        trace.append(
            {
                "iter": k,
                "lambda_c": float(np.exp(x_new[0])),
                "lambda_a": float(np.exp(x_new[1])),
                "land_pct": float(d[0]),
                "feed_pct": float(d[1]),
                "resid_inf": float(np.max(np.abs(r_new))),
            }
        )
        logger.info(
            "iter %d: lambda=(%.5g, %.5g)  d=(%.3f%%, %.3f%%)  |r|_inf=%.4f",
            k,
            np.exp(x_new[0]),
            np.exp(x_new[1]),
            d[0],
            d[1],
            np.max(np.abs(r_new)),
        )

        # Broyden "good" update on the Jacobian.
        s = dx
        y = r_new - r
        denom = float(s @ s)
        if denom > 1e-12:
            J = J + np.outer(y - J @ s, s) / denom

        x = x_new
        r = r_new

    return x, trace


def main() -> None:
    smk = snakemake  # type: ignore[name-defined]

    target_pct = float(smk.params.target_pct)
    name = smk.params.name

    seed_c = float(smk.params.seed_land_l1_cost)
    seed_a = float(smk.params.seed_animal_feed_l1_cost)
    tol = float(smk.params.tolerance)  # interpreted as |log(d/t)| tolerance
    max_iter = int(smk.params.max_iter)
    trust_log = float(smk.params.trust_region_log)

    # If a previously calibrated YAML exists at the configured path, warm-
    # start from it. The path is passed as a param (not an input) to avoid
    # a Snakemake self-loop with calibrated_l1_yaml.
    prev_yaml = getattr(smk.params, "previous_yaml", None)
    if prev_yaml and Path(prev_yaml).exists():
        with open(prev_yaml) as f:
            prev = yaml.safe_load(f)
        try:
            seed_c = float(prev["land_l1_cost"])
            seed_a = float(prev["animal_feed_l1_cost"])
        except KeyError as exc:
            raise ValueError(
                f"Previous calibration YAML at {prev_yaml} is missing key "
                f"{exc}; delete the file to start fresh."
            ) from exc
        logger.info(
            "Warm-starting from previous calibration: "
            "land_l1_cost=%.6g, animal_feed_l1_cost=%.6g",
            seed_c,
            seed_a,
        )

    # Construct the base_config and path_roots for build_scenario_entry. The
    # rule's config is already merged; we drop any pre-existing scenarios so
    # the dynamically-generated ones aren't shadowed.
    base_config = dict(smk.config)
    base_config.pop("scenarios", None)
    path_roots = default_path_roots(base_config)

    x0 = np.array([np.log(seed_c), np.log(seed_a)])
    # Diagonal Jacobian prior: in log-log, ∂log d_i / ∂log λ_i ≈ -1 if d ∝ 1/λ.
    J0 = np.diag([-1.0, -1.0])

    def evaluate_fn(lambda_c, lambda_a, *, iter_id):
        return _evaluate(
            lambda_c,
            lambda_a,
            iter_id=iter_id,
            base_config=base_config,
            name=name,
            path_roots=path_roots,
        )

    x_final, trace = broyden_iterate(
        evaluate_fn,
        x0,
        J0,
        target=target_pct,
        tol=tol,
        max_iter=max_iter,
        trust_log=trust_log,
    )
    lambda_c = float(np.exp(x_final[0]))
    lambda_a = float(np.exp(x_final[1]))
    final_resid = trace[-1]["resid_inf"]

    converged = final_resid < tol
    if not converged:
        logger.warning(
            "Did not converge within %d iterations: |r|_inf=%.4f (tol=%.4f)",
            max_iter,
            final_resid,
            tol,
        )

    # Write trace CSV for diagnostics.
    trace_path = Path(smk.output.trace)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trace).to_csv(trace_path, index=False)
    logger.info("Wrote iteration trace to %s", trace_path)

    out_path = Path(smk.output.calibrated_l1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek\n"
        "#\n"
        "# SPDX-License-Identifier: CC-BY-4.0\n"
        "#\n"
        "# Auto-generated by workflow/scripts/calibrate_prod_stability.py.\n"
        "# Consumed at solve time when production_stability.land_l1_cost\n"
        '# or .animal_feed_l1_cost is set to the sentinel string "calibrated".\n'
        "# Do not edit by hand -- run ``tools/calibrate stability`` to regenerate.\n"
    )
    body = yaml.safe_dump(
        {
            "target_deviation_pct": target_pct,
            # Mode the L1 coefficients were fit in. The validator in
            # workflow.validation.calibration gates the "calibrated"
            # sentinel against the consuming config to make sure these
            # coefficients are only resolved in the same regime.
            "penalty_mode": "l1",
            "deviation_type": "absolute",
            "land_l1_cost": lambda_c,
            "animal_feed_l1_cost": lambda_a,
            "iterations": len(trace) - 1,
            "converged": bool(converged),
            "final_residual_log_inf": float(final_resid),
        },
        sort_keys=False,
    )
    out_path.write_text(header + body)
    logger.info(
        "Wrote calibrated L1 costs to %s "
        "(land=%.6g, animal_feed=%.6g, iters=%d, converged=%s)",
        out_path,
        lambda_c,
        lambda_a,
        len(trace) - 1,
        converged,
    )


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )
    logging.getLogger("pypsa.optimization.optimize").addFilter(_ShadowPriceLogFilter())
    main()
