# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Retrieve GBD 2023 dietary relative-risk curves from the IHME Burden-of-Proof tool.

The Burden-of-Proof viz (https://vizhub.healthdata.org/burden-of-proof/) exposes
an undocumented JSON API that serves the age-aggregated exposure-response curve
for each (risk factor, cause) pair. No login is required: the data endpoints
sit behind Cloudflare's edge bot-check only, which a normal browser User-Agent
from a residential/university IP passes. (Automated cloud IPs may get a 403; in
that case run this rule from a normal machine.)

This rule downloads the raw curves once into the shared download cache. It is
config-independent: the (risk_factor -> rei_id) and (cause -> cause_id) maps come
from config.health.gbd_rei_id / gbd_cause_id (stable GBD identifiers, identical
across configs). We fetch every mapped (risk, cause) pair that the tool offers;
prepare_relative_risks.py later subsets to config.health.risk_cause_map, applies
the basis conversion, TMREL clipping, and age expansion.

Output columns (one row per curve point, GBD intake basis, "All Ages"):
    risk_factor, cause, exposure_g_per_day, rr_mean, rr_low, rr_high

IHME data carry a non-redistribution license; the downloaded file is gitignored
(data/downloads/) and must be fetched per-user.
"""

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://vizhub.healthdata.org/burden-of-proof/api/v1"
# A real browser User-Agent is required to pass the Cloudflare edge check.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://vizhub.healthdata.org/burden-of-proof/",
        }
    )
    return s


def _get(s: requests.Session, path: str, **params):
    r = s.get(f"{BASE}/{path}", params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_bop_curves(
    rei_by_risk: dict[str, int],
    cause_id_by_cause: dict[str, int],
) -> pd.DataFrame:
    """Fetch the all-ages RR curve for every available (risk, cause) pair."""
    s = _session()
    manifest = _get(s, "metadata/risk_cause")  # {rei_id: [cause_id, ...]}

    rows: list[dict] = []
    for risk_factor, rei in rei_by_risk.items():
        available = set(manifest.get(str(rei), []))
        for cause, cid in cause_id_by_cause.items():
            if cid not in available:
                logger.info(
                    "skip %s -> %s (rei %s, cause %s): not in BoP",
                    risk_factor,
                    cause,
                    rei,
                    cid,
                )
                continue
            meta = _get(s, "risk_cause_metadata", risk=rei, cause=cid)
            unit = meta.get("risk_unit")
            if unit != "g/day":
                raise ValueError(
                    f"{risk_factor}->{cause}: unexpected BoP exposure unit {unit!r} "
                    f"(only 'g/day' is supported)"
                )
            curve = _get(s, "output_data", risk=rei, cause=cid)
            xs = [float(p["risk"]) for p in curve]
            if xs != sorted(xs):
                raise ValueError(
                    f"{risk_factor}->{cause}: BoP exposure grid not ascending"
                )
            for p in curve:
                rr_mean = float(p["linear_cause"])
                rr_low = float(p["linear_cause_lower"])
                rr_high = float(p["linear_cause_upper"])
                if not (rr_low > 0 and rr_mean > 0 and rr_high > 0):
                    raise ValueError(
                        f"{risk_factor}->{cause}: non-positive RR at x={p['risk']}"
                    )
                rows.append(
                    {
                        "risk_factor": risk_factor,
                        "cause": cause,
                        "exposure_g_per_day": float(p["risk"]),
                        "rr_mean": rr_mean,
                        "rr_low": rr_low,
                        "rr_high": rr_high,
                    }
                )
            logger.info("%s -> %s: %d points", risk_factor, cause, len(curve))

    df = pd.DataFrame(
        rows,
        columns=[
            "risk_factor",
            "cause",
            "exposure_g_per_day",
            "rr_mean",
            "rr_low",
            "rr_high",
        ],
    )
    return df.sort_values(["risk_factor", "cause", "exposure_g_per_day"]).reset_index(
        drop=True
    )


def main() -> None:
    health_cfg = snakemake.params["health"]
    rei_by_risk = {str(k): int(v) for k, v in health_cfg["gbd_rei_id"].items()}
    cause_id_by_cause = {str(k): int(v) for k, v in health_cfg["gbd_cause_id"].items()}

    df = fetch_bop_curves(rei_by_risk, cause_id_by_cause)
    if df.empty:
        raise ValueError("No Burden-of-Proof curves fetched")

    out = Path(snakemake.output["curves"])
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    logger.info(
        "Wrote %d rows (%d pairs) to %s",
        len(df),
        df.groupby(["risk_factor", "cause"]).ngroups,
        out,
    )


if __name__ == "__main__":
    from workflow.scripts.logging_config import setup_script_logging

    logger = setup_script_logging(log_file=snakemake.log[0] if snakemake.log else None)
    main()
