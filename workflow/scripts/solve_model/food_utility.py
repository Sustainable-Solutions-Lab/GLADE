# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Piecewise food utility helpers for solve-time objective augmentation."""

import logging

from linopy.constants import BREAKPOINT_DIM
import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)

# Sentinel x-coordinate used to extend the last utility segment past the
# right-most real breakpoint.  Linopy's LP piecewise formulation imposes
# a `link_p <= max(x)` domain bound; placing the sentinel far beyond any
# plausible consumption makes this bound effectively inert while keeping
# the last-segment slope equal to the last-block marginal utility (the
# "overflow continuation" behaviour the model relies on).
OVERFLOW_SENTINEL_MT: float = 1.0e6


def _prepare_piecewise_utility_rows(
    utility_df: pd.DataFrame,
    consume_links: pd.DataFrame,
) -> pd.DataFrame:
    """Validate and align utility rows to existing food consumption link names."""
    required = {
        "food",
        "country",
        "block_id",
        "width_mt_per_year",
        "marginal_utility_bnusd_per_mt",
    }
    missing = required - set(utility_df.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"Missing required utility block columns: {missing_text}")

    df = utility_df.copy()
    df["food"] = df["food"].astype(str)
    df["country"] = df["country"].astype(str).str.upper()
    df["block_id"] = pd.to_numeric(df["block_id"], errors="raise").astype(int)
    df["width_mt_per_year"] = pd.to_numeric(df["width_mt_per_year"], errors="coerce")
    df["marginal_utility_bnusd_per_mt"] = pd.to_numeric(
        df["marginal_utility_bnusd_per_mt"], errors="coerce"
    )

    df = df.dropna(subset=["width_mt_per_year", "marginal_utility_bnusd_per_mt"])
    df = df[df["width_mt_per_year"] > 0].copy()
    if df.empty:
        logger.info("No valid piecewise utility rows after filtering")
        return df

    consume_map = consume_links.reset_index()[["name", "food", "country"]].copy()
    merged = df.merge(consume_map, on=["food", "country"], how="inner")
    if merged.empty:
        logger.info("No utility rows match food_consumption links")
        return merged

    duplicate_mask = merged.duplicated(subset=["name", "block_id"], keep=False)
    if duplicate_mask.any():
        sample = merged.loc[duplicate_mask, ["name", "block_id"]].head(5)
        raise ValueError(
            "Duplicate piecewise utility rows for (link, block_id). "
            f"Examples:\n{sample.to_string(index=False)}"
        )

    for name, group in merged.groupby("name"):
        ordered = group.sort_values("block_id")
        expected = list(range(1, len(ordered) + 1))
        observed = ordered["block_id"].tolist()
        if observed != expected:
            raise ValueError(
                "Block ids must be contiguous starting at 1 for each link. "
                f"Link '{name}' has blocks {observed}"
            )

        utilities = ordered["marginal_utility_bnusd_per_mt"].to_numpy()
        if len(utilities) > 1 and (utilities[1:] > utilities[:-1]).any():
            raise ValueError(
                "Piecewise utility must be non-increasing by block_id for each link. "
                f"Link '{name}' violates this rule."
            )

    return merged[
        ["name", "block_id", "width_mt_per_year", "marginal_utility_bnusd_per_mt"]
    ]


def _merge_small_width_blocks(
    rows: pd.DataFrame, min_width_mt: float
) -> tuple[pd.DataFrame, int, int]:
    """Merge small-width utility blocks into adjacent blocks per link.

    The merged block keeps total utility mass using a width-weighted average
    marginal utility and block ids are renumbered contiguously starting at 1.
    """
    if rows.empty or min_width_mt <= 0:
        return rows.copy(), 0, 0

    # Fast path: short-circuit when no block falls below the threshold.
    if (rows["width_mt_per_year"] >= min_width_mt).all():
        return rows.copy(), 0, 0

    # Sort once globally; then walk groups via index slicing rather than groupby
    # DataFrame iteration to avoid thousands of per-link DataFrame allocations.
    ordered = rows.sort_values(["name", "block_id"], kind="stable").reset_index(
        drop=True
    )
    names_arr = ordered["name"].to_numpy()
    widths_arr = ordered["width_mt_per_year"].to_numpy(dtype=float)
    utils_arr = ordered["marginal_utility_bnusd_per_mt"].to_numpy(dtype=float)

    # Group boundaries via change-points on the sorted name column.
    n_rows = len(names_arr)
    group_change = np.empty(n_rows, dtype=bool)
    group_change[0] = True
    group_change[1:] = names_arr[1:] != names_arr[:-1]
    group_starts = np.flatnonzero(group_change)
    group_ends = np.empty_like(group_starts)
    group_ends[:-1] = group_starts[1:]
    group_ends[-1] = n_rows

    out_names_chunks: list[np.ndarray] = []
    out_blocks_chunks: list[np.ndarray] = []
    out_widths_chunks: list[np.ndarray] = []
    out_utils_chunks: list[np.ndarray] = []

    merged_blocks = 0
    affected_links = 0

    for s, e in zip(group_starts, group_ends):
        n_blocks = e - s
        w_slice = widths_arr[s:e]
        u_slice = utils_arr[s:e]

        # Pass-through fast path: nothing to merge for this link.
        if n_blocks <= 1 or (w_slice >= min_width_mt).all():
            out_names_chunks.append(np.full(n_blocks, names_arr[s], dtype=object))
            out_blocks_chunks.append(np.arange(1, n_blocks + 1))
            out_widths_chunks.append(w_slice)
            out_utils_chunks.append(u_slice)
            continue

        # Slow path: iterative merge of small blocks (n is small per link).
        widths = w_slice.tolist()
        utilities = u_slice.tolist()
        link_merged = 0
        while len(widths) > 1:
            donor = next(
                (i for i, w in enumerate(widths) if w < min_width_mt),
                -1,
            )
            if donor < 0:
                break
            recv = donor - 1 if donor > 0 else 1
            total_w = widths[recv] + widths[donor]
            utilities[recv] = (
                utilities[recv] * widths[recv] + utilities[donor] * widths[donor]
            ) / total_w
            widths[recv] = total_w
            del widths[donor]
            del utilities[donor]
            link_merged += 1

        if link_merged > 0:
            merged_blocks += link_merged
            affected_links += 1

        n_out = len(widths)
        out_names_chunks.append(np.full(n_out, names_arr[s], dtype=object))
        out_blocks_chunks.append(np.arange(1, n_out + 1))
        out_widths_chunks.append(np.asarray(widths, dtype=float))
        out_utils_chunks.append(np.asarray(utilities, dtype=float))

    result = pd.DataFrame(
        {
            "name": np.concatenate(out_names_chunks),
            "block_id": np.concatenate(out_blocks_chunks),
            "width_mt_per_year": np.concatenate(out_widths_chunks),
            "marginal_utility_bnusd_per_mt": np.concatenate(out_utils_chunks),
        }
    )
    return result, merged_blocks, affected_links


def _build_cumulative_breakpoints(
    rows: pd.DataFrame, link_names: list[str]
) -> tuple[xr.DataArray, xr.DataArray, list[int]]:
    """Build per-link (x, y) breakpoints for the cumulative utility curve.

    For each link with non-increasing marginal utilities ``mu_1 .. mu_K``
    and block widths ``w_1 .. w_K``:

        x_0 = 0,  x_k = sum_{i=1..k} w_i
        y_0 = 0,  y_k = sum_{i=1..k} mu_i * w_i

    A final sentinel breakpoint at x = OVERFLOW_SENTINEL_MT extends the
    last segment with slope mu_K, so consumption past the last block
    earns the last-block marginal utility.

    Returns DataArrays with dims ``(name, BREAKPOINT_DIM)`` and NaN
    padding so per-link breakpoint counts can differ (linopy masks NaN
    pieces in the LP method).
    """
    n_links = len(link_names)
    # Pre-sort once and split via boundaries to avoid per-link groupby cost.
    ordered = rows.sort_values(["name", "block_id"], kind="stable")
    name_to_idx = {name: i for i, name in enumerate(link_names)}

    # Collect per-link arrays.
    x_arrays: list[np.ndarray] = [np.empty(0)] * n_links
    y_arrays: list[np.ndarray] = [np.empty(0)] * n_links
    block_counts: list[int] = [0] * n_links

    for name, sub in ordered.groupby("name", sort=False):
        i = name_to_idx[name]
        w = sub["width_mt_per_year"].to_numpy(dtype=float)
        mu = sub["marginal_utility_bnusd_per_mt"].to_numpy(dtype=float)
        cum_x = np.concatenate(([0.0], np.cumsum(w)))
        cum_y = np.concatenate(([0.0], np.cumsum(mu * w)))
        last_mu = float(mu[-1])
        sentinel_y = float(cum_y[-1] + last_mu * (OVERFLOW_SENTINEL_MT - cum_x[-1]))
        x_arrays[i] = np.concatenate((cum_x, [OVERFLOW_SENTINEL_MT]))
        y_arrays[i] = np.concatenate((cum_y, [sentinel_y]))
        block_counts[i] = len(w)

    # Pad to a common breakpoint dimension with NaN; trailing-NaN pieces are
    # masked out by linopy's LP method.
    max_len = max(arr.size for arr in x_arrays)
    x_padded = np.full((n_links, max_len), np.nan, dtype=float)
    y_padded = np.full((n_links, max_len), np.nan, dtype=float)
    for i, (xs, ys) in enumerate(zip(x_arrays, y_arrays)):
        x_padded[i, : xs.size] = xs
        y_padded[i, : ys.size] = ys

    coords = {"name": link_names, BREAKPOINT_DIM: np.arange(max_len)}
    x_pts = xr.DataArray(x_padded, coords=coords, dims=("name", BREAKPOINT_DIM))
    y_pts = xr.DataArray(y_padded, coords=coords, dims=("name", BREAKPOINT_DIM))
    return x_pts, y_pts, block_counts


def add_piecewise_food_utility(
    n: pypsa.Network, utility_blocks_path: str, min_block_width_mt: float
) -> None:
    """Add piecewise diminishing marginal utility for food consumption links.

    Adds one auxiliary ``food_utility_value`` variable per link, bounded
    above by the per-link concave cumulative-utility curve via linopy's
    LP tangent-line piecewise formulation (one chord inequality per
    segment).  The objective receives ``-sum(utility)``, so the LP
    pushes each link's utility up to the curve value at the optimal
    consumption level.

    The last segment is extended with a sentinel breakpoint at
    ``OVERFLOW_SENTINEL_MT`` so consumption past the last real block
    earns the last-block marginal utility (the "overflow continuation"
    behaviour the model relies on for negative-MU foods).
    """
    m = n.model
    if m is None:
        raise ValueError("Linopy model is not initialized; call after create_model()")

    utility_df = pd.read_csv(utility_blocks_path)
    if utility_df.empty:
        logger.info("Utility blocks file is empty: %s", utility_blocks_path)
        return

    consume_links = n.links.static[
        n.links.static["carrier"] == "food_consumption"
    ].copy()
    if consume_links.empty:
        logger.info("No food_consumption links found; skipping piecewise utility")
        return

    all_rows = _prepare_piecewise_utility_rows(utility_df, consume_links)
    if all_rows.empty:
        return

    covered_links = set(all_rows["name"].astype(str))
    missing_links = [
        name for name in consume_links.index if str(name) not in covered_links
    ]
    if missing_links:
        logger.info(
            "Piecewise utility omits %d of %d food_consumption links "
            "(no baseline consumption); these earn zero utility",
            len(missing_links),
            len(consume_links),
        )

    min_width = float(min_block_width_mt)
    rows, merged_rows, affected_links = _merge_small_width_blocks(all_rows, min_width)
    if merged_rows > 0:
        logger.info(
            "Piecewise utility width floor %.6g Mt/year merged %d small blocks across %d links",
            min_width,
            merged_rows,
            affected_links,
        )

    remaining_small = int((rows["width_mt_per_year"] < min_width).sum())
    if remaining_small > 0:
        logger.info(
            "Piecewise utility width floor %.6g Mt/year left %d unmergeable blocks",
            min_width,
            remaining_small,
        )

    link_names = sorted(rows["name"].unique().tolist())
    x_pts, y_pts, block_counts = _build_cumulative_breakpoints(rows, link_names)

    # One utility variable per link.  No bounds: chord constraints pin it
    # from above and the objective pulls it up; a negative lower bound is
    # required because some calibrated marginal utilities are negative
    # (in which case the integrated utility goes negative beyond the
    # break-even point).
    utility_var = m.add_variables(
        lower=-np.inf,
        coords=[link_names],
        dims=["name"],
        name="food_utility_value",
    )

    link_p = m.variables["Link-p"].sel(snapshot="now").sel(name=link_names)

    # Concave curve bounded above: utility <= f(link_p), where f is the
    # integral of the non-increasing marginal-utility schedule.  linopy's
    # "auto" method selects the LP tangent-line formulation here.
    m.add_piecewise_formulation(
        (utility_var, y_pts, "<="),
        (link_p, x_pts),
        name="food_utility_piecewise",
    )

    m.objective += -utility_var.sum()

    n.meta["food_utility_piecewise"] = {
        "links": len(link_names),
        "blocks_per_link_max": int(max(block_counts)),
        "blocks_per_link_min": int(min(block_counts)),
        "total_width_mt_per_year": float(rows["width_mt_per_year"].sum()),
    }

    logger.info(
        "Applied piecewise utility to %d food consumption links (%d-%d blocks each)",
        len(link_names),
        min(block_counts),
        max(block_counts),
    )


def pop_piecewise_food_utility_value(n: pypsa.Network) -> float:
    """Return the realized piecewise food-utility value from the solved model."""
    m = n.model
    if m is None or "food_utility_value" not in m.variables:
        return 0.0
    sol = m.variables["food_utility_value"].solution
    if sol is None:
        return 0.0
    return float(sol.sum().item())
