# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Piecewise food utility helpers for solve-time objective augmentation."""

import logging

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)

FOOD_UTILITY_COEFFS: dict[int, tuple[xr.DataArray, xr.DataArray]] = {}


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


def add_piecewise_food_utility(
    n: pypsa.Network, utility_blocks_path: str, min_block_width_mt: float
) -> None:
    """Add piecewise diminishing marginal utility for food consumption links.

    Adds variables per link and block with upper bounds from
    ``width_mt_per_year`` and constrains each link's food consumption to the sum
    over these block variables plus a non-incentivized overflow variable.
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
        sample = ", ".join(str(name) for name in missing_links[:5])
        raise ValueError(
            "Piecewise utility must cover all food_consumption links. "
            f"Missing {len(missing_links)} links (examples: {sample})"
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

    block_ids = sorted(rows["block_id"].unique().tolist())
    link_names = sorted(rows["name"].unique().tolist())

    width_matrix = (
        rows.pivot(index="name", columns="block_id", values="width_mt_per_year")
        .reindex(index=link_names, columns=block_ids)
        .fillna(0.0)
        .astype(float)
    )
    utility_matrix = (
        rows.pivot(
            index="name", columns="block_id", values="marginal_utility_bnusd_per_mt"
        )
        .reindex(index=link_names, columns=block_ids)
        .fillna(0.0)
        .astype(float)
    )

    link_p = m.variables["Link-p"].sel(snapshot="now")

    widths = xr.DataArray(
        width_matrix.to_numpy(),
        coords={"name": link_names, "block_id": block_ids},
        dims=("name", "block_id"),
    )
    utilities = xr.DataArray(
        utility_matrix.to_numpy(),
        coords={"name": link_names, "block_id": block_ids},
        dims=("name", "block_id"),
    )

    block_flow = m.add_variables(
        lower=0.0,
        upper=widths,
        coords=[link_names, block_ids],
        dims=["name", "block_id"],
        name="food_utility_block_flow",
    )
    overflow = m.add_variables(
        lower=0.0,
        coords=[link_names],
        dims=["name"],
        name="food_utility_overflow_flow",
    )

    m.add_constraints(
        link_p.sel(name=link_names) == block_flow.sum("block_id") + overflow,
        name="GlobalConstraint-food_utility_piecewise_balance",
    )

    # Continue the utility schedule into overflow to prevent the solver from
    # bypassing blocks.  Without this, overflow has zero utility, which is
    # more attractive than any negative-utility block - causing the solver to
    # route all consumption of negative-mu foods through overflow and
    # effectively ignoring the piecewise schedule.
    overflow_utilities = utilities.isel(block_id=-1)

    # Combine both contributions into a single objective update.  Each
    # m.objective += <expr> merges <expr> with the full existing objective,
    # so issuing two of them doubles that O(N_obj_terms) merge cost.
    m.objective += -(
        (utilities * block_flow).sum() + (overflow_utilities * overflow).sum()
    )

    FOOD_UTILITY_COEFFS[id(m)] = (utilities, overflow_utilities)

    block_counts = rows.groupby("name")["block_id"].max()
    n.meta["food_utility_piecewise"] = {
        "links": len(link_names),
        "blocks_per_link_max": int(block_counts.max()),
        "blocks_per_link_min": int(block_counts.min()),
        "total_width_mt_per_year": float(widths.sum().item()),
    }

    logger.info(
        "Applied piecewise utility to %d food consumption links (%d-%d blocks each)",
        len(link_names),
        int(block_counts.min()),
        int(block_counts.max()),
    )


def pop_piecewise_food_utility_value(n: pypsa.Network) -> float:
    """Return realized utility value and clear cached coefficients."""
    m = n.model
    if m is None:
        return 0.0

    cached = FOOD_UTILITY_COEFFS.pop(id(m), None)
    if cached is None:
        return 0.0
    block_coeffs, overflow_coeffs = cached
    if "food_utility_block_flow" not in m.variables:
        return 0.0

    block_sol = m.variables["food_utility_block_flow"].solution
    if block_sol is None:
        return 0.0

    total = float((block_coeffs * block_sol).sum().item())

    overflow_sol = m.variables["food_utility_overflow_flow"].solution
    if overflow_sol is not None:
        total += float((overflow_coeffs * overflow_sol).sum().item())

    return total
