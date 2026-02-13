# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Piecewise food utility helpers for solve-time objective augmentation."""

import logging

import pandas as pd
import pypsa
import xarray as xr

logger = logging.getLogger(__name__)

FOOD_UTILITY_COEFFS: dict[int, xr.DataArray] = {}


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

    merged_groups: list[pd.DataFrame] = []
    merged_blocks = 0
    affected_links = 0

    for name, group in rows.groupby("name", sort=False):
        ordered = group.sort_values("block_id")
        blocks = [
            {
                "width": float(width),
                "utility": float(utility),
            }
            for width, utility in zip(
                ordered["width_mt_per_year"],
                ordered["marginal_utility_bnusd_per_mt"],
                strict=True,
            )
        ]

        link_merged = 0
        while len(blocks) > 1:
            donor_idx = next(
                (i for i, block in enumerate(blocks) if block["width"] < min_width_mt),
                None,
            )
            if donor_idx is None:
                break

            receiver_idx = donor_idx - 1 if donor_idx > 0 else 1
            donor = blocks[donor_idx]
            receiver = blocks[receiver_idx]

            total_width = receiver["width"] + donor["width"]
            receiver["utility"] = (
                receiver["utility"] * receiver["width"]
                + donor["utility"] * donor["width"]
            ) / total_width
            receiver["width"] = total_width

            del blocks[donor_idx]
            link_merged += 1

        if link_merged > 0:
            merged_blocks += link_merged
            affected_links += 1

        merged_groups.append(
            pd.DataFrame(
                {
                    "name": name,
                    "block_id": range(1, len(blocks) + 1),
                    "width_mt_per_year": [block["width"] for block in blocks],
                    "marginal_utility_bnusd_per_mt": [
                        block["utility"] for block in blocks
                    ],
                }
            )
        )

    return pd.concat(merged_groups, ignore_index=True), merged_blocks, affected_links


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
    m.objective += -(utilities * block_flow).sum()
    FOOD_UTILITY_COEFFS[id(m)] = utilities

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

    coeffs = FOOD_UTILITY_COEFFS.pop(id(m), None)
    if coeffs is None:
        return 0.0
    if "food_utility_block_flow" not in m.variables:
        return 0.0

    solution = m.variables["food_utility_block_flow"].solution
    if solution is None:
        return 0.0

    return float((coeffs * solution).sum().item())
