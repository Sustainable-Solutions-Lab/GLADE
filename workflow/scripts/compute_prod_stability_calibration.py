# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compute calibrated L1 production-stability penalty costs.

Consumes the ``baseline_deviation.parquet`` files produced for every
``grid_c{crop_cost}_a{animal_cost}`` scenario of the ``prod_stability``
grid sweep, locates the pair :math:`(\\ell^c_1, \\ell^a_1)` at which both
the land-use deviation and the animal-feed deviation equal the target
percentage (default 5 % of the observed baseline totals), and writes the
result to a YAML file that is read at solve time.

The algorithm is a faithful port of the logic in
``notebooks/prod_stability_calibration.ipynb`` — log-linear 1-D
interpolation of the two 5 % contours, followed by a fixed-point
iteration that picks out their intersection in the
:math:`(\\ell^c_1, \\ell^a_1)` plane. No rounding is applied.
"""

import logging
from pathlib import Path
import re

import numpy as np
import pandas as pd
import yaml

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


GRID_SCENARIO_RE = re.compile(r"grid_c([0-9p]+)_a([0-9p]+)$")


def _parse_grid_scenario(name: str) -> tuple[float, float] | None:
    m = GRID_SCENARIO_RE.match(name)
    if m is None:
        return None
    return (
        float(m.group(1).replace("p", ".")),
        float(m.group(2).replace("p", ".")),
    )


def _interp_cost(costs: np.ndarray, devs: np.ndarray, target: float) -> float:
    """Log-linear interpolation: cost at which deviation crosses ``target``.

    Returns NaN if the target is outside the observed deviation range.
    """
    costs = np.asarray(costs, dtype=float)
    devs = np.asarray(devs, dtype=float)
    order = np.argsort(devs)
    lo, hi = devs[order[0]], devs[order[-1]]
    if not lo <= target <= hi:
        return float("nan")
    return float(
        np.exp(
            np.interp(
                np.log(target),
                np.log(devs[order]),
                np.log(costs[order]),
            )
        )
    )


def _interp_xy(index: np.ndarray, values: np.ndarray, x: float) -> float:
    """Log-linear interpolation of a 1-D function evaluated at ``x``."""
    keys = np.log(np.asarray(index, dtype=float))
    vals = np.log(np.asarray(values, dtype=float))
    ok = np.isfinite(vals)
    return float(np.exp(np.interp(np.log(x), keys[ok], vals[ok])))


def compute_calibration(
    deviation_paths: list[str], target_pct: float
) -> dict[str, float]:
    """Compute calibrated ``(crop_l1_cost, animal_l1_cost)`` from a grid.

    Parameters
    ----------
    deviation_paths : list[str]
        One ``baseline_deviation.parquet`` path per ``grid_c*_a*``
        scenario. The scenario name is read from the parent directory
        (``scen-grid_c{crop_cost}_a{animal_cost}``).
    target_pct : float
        Target deviation, in percent of the observed baseline total.
    """
    records: list[dict[str, float]] = []
    for raw in deviation_paths:
        path = Path(raw)
        scen_name = path.parent.name.removeprefix("scen-")
        parsed = _parse_grid_scenario(scen_name)
        if parsed is None:
            raise ValueError(
                f"Unexpected scenario name '{scen_name}' for prod-stability "
                f"calibration input: {path}"
            )
        crop_cost, animal_cost = parsed
        df = pd.read_parquet(path).set_index("component")

        land_bl = (
            df.loc["crop_area", "baseline_total"]
            + df.loc["pasture_area", "baseline_total"]
        )
        land_dev = (
            df.loc["crop_area", "abs_deviation"]
            + df.loc["pasture_area", "abs_deviation"]
        )
        feed_bl = df.loc["animal_feed_use", "baseline_total"]
        feed_dev = df.loc["animal_feed_use", "abs_deviation"]

        records.append(
            {
                "crop_cost": crop_cost,
                "animal_cost": animal_cost,
                "land_pct": 100 * land_dev / land_bl,
                "feed_pct": 100 * feed_dev / feed_bl,
                "land_bl_mha": float(land_bl),
                "feed_bl_mt": float(feed_bl),
            }
        )

    results = (
        pd.DataFrame(records)
        .sort_values(["crop_cost", "animal_cost"])
        .reset_index(drop=True)
    )
    n_crop = results["crop_cost"].nunique()
    n_animal = results["animal_cost"].nunique()
    if len(results) != n_crop * n_animal:
        raise ValueError(
            f"Incomplete grid: {len(results)} scenarios but expected "
            f"{n_crop} x {n_animal} = {n_crop * n_animal}."
        )
    logger.info(
        "Loaded %d grid scenarios (%d crop_cost x %d animal_cost values)",
        len(results),
        n_crop,
        n_animal,
    )
    logger.info(
        "Baseline totals: land=%.1f Mha, feed=%.1f Mt DM",
        results.iloc[0]["land_bl_mha"],
        results.iloc[0]["feed_bl_mt"],
    )

    # feed-target contour: per crop_cost row, animal_cost where feed_pct = target
    feed_contour = pd.Series(
        {
            cc: _interp_cost(
                sub["animal_cost"].to_numpy(),
                sub["feed_pct"].to_numpy(),
                target_pct,
            )
            for cc, sub in results.groupby("crop_cost")
        }
    ).sort_index()

    # land-target contour: per animal_cost column, crop_cost where land_pct = target
    land_contour = pd.Series(
        {
            ac: _interp_cost(
                sub["crop_cost"].to_numpy(),
                sub["land_pct"].to_numpy(),
                target_pct,
            )
            for ac, sub in results.groupby("animal_cost")
        }
    ).sort_index()

    if not np.isfinite(feed_contour).any() or not np.isfinite(land_contour).any():
        raise ValueError(
            f"Target deviation {target_pct}% is outside the grid range on at "
            "least one axis. Widen the grid in config/calibration/stability.yaml."
        )

    # Fixed-point iteration: cc -> animal_cost at feed-target -> crop_cost at land-target.
    cc = float(feed_contour.index[np.abs(feed_contour.values - 0.033).argmin()])
    for _ in range(100):
        ac = _interp_xy(
            feed_contour.index.to_numpy(),
            feed_contour.to_numpy(),
            cc,
        )
        cc_new = _interp_xy(
            land_contour.index.to_numpy(),
            land_contour.to_numpy(),
            ac,
        )
        if abs(np.log(cc_new) - np.log(cc)) < 1e-6:
            cc = cc_new
            break
        cc = cc_new
    else:
        raise RuntimeError(
            "Contour-intersection fixed point did not converge within 100 iterations."
        )
    ac = _interp_xy(feed_contour.index.to_numpy(), feed_contour.to_numpy(), cc)

    logger.info(
        "Contour intersection at target %.1f%%: land_l1_cost=%.6f, "
        "animal_feed_l1_cost=%.6f",
        target_pct,
        cc,
        ac,
    )
    return {"land_l1_cost": float(cc), "animal_feed_l1_cost": float(ac)}


def main() -> None:
    target_pct = float(snakemake.params.target_pct)
    deviation_paths = list(snakemake.input.grid_deviations)
    logger.info(
        "Computing prod-stability calibration from %d grid scenarios",
        len(deviation_paths),
    )

    result = compute_calibration(deviation_paths, target_pct)

    out_path = Path(snakemake.output.calibrated_l1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek\n"
        "#\n"
        "# SPDX-License-Identifier: CC-BY-4.0\n"
        "#\n"
        "# Auto-generated by workflow/scripts/compute_prod_stability_calibration.py.\n"
        "# Consumed at solve time when production_stability.land_l1_cost\n"
        '# or .animal_feed_l1_cost is set to the sentinel string "calibrated".\n'
        "# Do not edit by hand -- run ``tools/calibrate stability`` to regenerate.\n"
    )
    body = yaml.safe_dump(
        {
            "target_deviation_pct": target_pct,
            "land_l1_cost": result["land_l1_cost"],
            "animal_feed_l1_cost": result["animal_feed_l1_cost"],
        },
        sort_keys=False,
    )
    out_path.write_text(header + body)
    logger.info("Wrote calibrated L1 costs to %s", out_path)


if __name__ == "__main__":
    logger = setup_script_logging(
        log_file=snakemake.log[0] if snakemake.log else None  # type: ignore[name-defined]
    )
    main()
