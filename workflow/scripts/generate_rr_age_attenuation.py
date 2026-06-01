# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the curated dietary RR age-attenuation table (one-off).

The IHME Burden-of-Proof tool only serves age-aggregated ("All Ages") dietary
relative-risk curves. GBD applies age-specific RRs for cardiovascular outcomes
(the proportional effect attenuates with age); diabetes and colorectal cancer
carry no age attenuation. We reconstruct that age structure once and freeze it
into a curated table so the per-build workflow no longer depends on the GBD 2019
relative-risk workbook.

Method
------
The log-RR age attenuation is multiplicative and essentially exposure-independent:

    log RR_age(x) ~= beta(age) * log RR_ref(x)

Per the GBD 2021/2023 risk-factors capstone appendix, GBD assigns the estimated
(MR-BRT / Burden-of-Proof) risk curve to a *reference age group* -- the median
age-at-event across cohorts, 60-64 years for the cardiovascular age trend (the
same trend is applied to dietary CVD outcomes) -- and derives age-specific RRs
by attenuation relative to it. The Burden-of-Proof tool's "All Ages" curve is
therefore the 60-64 reference-age curve. We mirror that exactly: take the GBD
2019 age shape and normalize it to the 60-64 reference,

    beta(risk, cause, a) = log RR(a) / log RR(60-64)

so age-expanding the BoP curve reproduces it at 60-64, attenuates to older ages,
and amplifies to younger ages, as GBD does. T2DM and CRC carry no age
attenuation (beta = 1).

(Caveat: GBD attenuates *excess* risk, RR - 1, with a percentage-change form in
GBD 2023, whereas we attenuate log-RR. Because beta is measured from GBD's
*published* age-specific curves -- which already bake in that attenuation -- and
we only re-set the reference age, the functional-form difference is second-order.)

Provenance: age shape is indirect from the GBD 2019 RR appendix; the 60-64
reference age is GBD's documented choice. Our own derived result, not a
redistribution of GBD RR values.

Run once from the repo root:

    pixi run python workflow/scripts/generate_rr_age_attenuation.py
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from workflow.scripts.gbd2019_rr_appendix import (
    ADULT_AGE_LABELS,
    parse_gbd2019_rr_appendix,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

GBD2019_RR_XLSX = (
    "data/manually_downloaded/IHME_GBD_2019_RELATIVE_RISKS_Y2020M10D15.XLSX"
)
OUTPUT = "data/curated/health/rr_age_attenuation.csv"

# (risk_factor -> causes) for which we need an age structure. red_meat is kept
# on its literature override but still needs its causes' age shape.
NEEDED = {
    "fruits": ["CHD", "Stroke", "T2DM"],
    "vegetables": ["CHD", "Stroke"],
    "nuts_seeds": ["CHD"],
    "legumes": ["CHD"],
    "red_meat": ["CHD", "Stroke", "T2DM", "CRC"],
    "whole_grains": ["CHD", "Stroke", "T2DM", "CRC"],
}
YOUNGEST_AGE = ADULT_AGE_LABELS[0]  # 25-29; numerically stable ratio denominator
REFERENCE_AGE = "60-64"  # GBD reference age group for the CVD age trend
_LOG_RR_EPS = 0.02  # ignore exposures where the reference log-RR is ~0 (unstable ratio)


def _extract_shape(rr19: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    """log-RR age shape, normalized to the youngest adult bucket (clamped [0, 1])."""
    shape: dict[tuple[str, str, str], float] = {}
    for (risk, cause), g in rr19.groupby(["risk_factor", "cause"]):
        piv = g.pivot_table(index="exposure_g_per_day", columns="age", values="rr_mean")
        missing = [a for a in ADULT_AGE_LABELS if a not in piv.columns]
        if missing:
            raise ValueError(f"GBD 2019 RR missing ages for {risk}->{cause}: {missing}")
        x = piv.index.values
        sel = x > 0
        log_young = np.log(piv[YOUNGEST_AGE].values[sel])
        stable = np.abs(log_young) >= _LOG_RR_EPS
        for age in ADULT_AGE_LABELS:
            ratios = np.log(piv[age].values[sel])[stable] / log_young[stable]
            val = 1.0 if ratios.size == 0 else float(np.median(ratios))
            shape[(risk, cause, age)] = max(0.0, min(1.0, val))
    return shape


def main() -> None:
    rr19 = parse_gbd2019_rr_appendix(
        pd.read_excel(GBD2019_RR_XLSX, header=None), ssb_sugar_per_gram=1.0
    )
    shape = _extract_shape(rr19)

    rows = []
    logger.info(
        f"{'risk->cause':24} {'beta[25-29]':>11} {'beta[60-64]':>11} {'beta[95+]':>9}"
    )
    for risk, causes in NEEDED.items():
        for cause in causes:
            ref = shape.get((risk, cause, REFERENCE_AGE))
            if not ref or ref <= 0:
                raise ValueError(
                    f"No usable {REFERENCE_AGE} reference for {risk}->{cause}"
                )
            for age in ADULT_AGE_LABELS:
                rows.append(
                    {
                        "risk_factor": risk,
                        "cause": cause,
                        "age": age,
                        "beta": shape[(risk, cause, age)] / ref,
                    }
                )
            young = shape[(risk, cause, YOUNGEST_AGE)] / ref
            old = shape[(risk, cause, ADULT_AGE_LABELS[-1])] / ref
            logger.info(
                f"  {risk + '->' + cause:22} {young:11.2f} {1.0:11.2f} {old:9.2f}"
            )

    out = pd.DataFrame(rows).sort_values(["risk_factor", "cause", "age"])
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT, index=False)
    logger.info(f"\nwrote {len(out)} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
