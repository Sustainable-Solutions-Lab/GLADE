# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Iterative calibration of L1 deviation-penalty coefficients.

Drives the per-component deviation percentages (any non-empty subset of
{land, feed, diet}) simultaneously to the target percentage via Broyden's
method in log-log coordinates. Each iteration is one paired baseline+main
solve, run in-process by the script.
"""


_dp_cal_cfg = config["deviation_penalty"]["calibration"]

if _dp_cal_cfg["generate"]:
    _trace_csv = _dp_cal_cfg["trace_csv"].format(name=name)

    rule calibrate_deviation_penalty:
        input:
            model=f"<results>/{name}/build/model.nc",
            baseline_diet=f"<processing>/{name}/baseline_diet.csv",
            m49="data/curated/M49-codes.csv",
            food_groups="data/curated/food_groups.csv",
            health_risk_breakpoints=f"<processing>/{name}/health/risk_breakpoints.csv",
            health_cluster_cause=f"<processing>/{name}/health/cluster_cause_baseline.csv",
            health_cause_log=f"<processing>/{name}/health/cause_log_breakpoints.csv",
            health_cluster_summary=f"<processing>/{name}/health/cluster_summary.csv",
            health_clusters=f"<processing>/{name}/health/country_clusters.csv",
            health_tmrel=f"<processing>/{name}/health/tmrel.csv",
            health_cluster_risk_baseline=f"<processing>/{name}/health/cluster_risk_baseline.csv",
            nutrition="data/curated/nutrition.csv",
        output:
            calibrated_yaml=_dp_cal_cfg["calibrated_yaml"],
            trace=_trace_csv,
        params:
            components=_dp_cal_cfg["components"],
            target_pct=_dp_cal_cfg["target_deviation_pct"],
            seeds=_dp_cal_cfg["seeds"],
            tolerance=_dp_cal_cfg["tolerance"],
            max_iter=_dp_cal_cfg["max_iter"],
            trust_region_log=_dp_cal_cfg["trust_region_log"],
            # Warm-start path is passed as a param (not an input) so Snakemake
            # doesn't create a self-loop on calibrated_yaml. The script loads
            # it iff the file exists on disk at run time.
            previous_yaml=_dp_cal_cfg["calibrated_yaml"],
            name=name,
        resources:
            # Per-iteration solves use mem_mb / runtime configured in the
            # solving section. Calibration itself is light; the bulk of
            # memory goes into the in-process pypsa.Network objects.
            runtime=lambda w, attempt: 60 * 60 * (1 + attempt),
            mem_mb=lambda w, attempt: 12000 * attempt,
        log:
            f"<logs>/{name}/calibrate_deviation_penalty.log",
        benchmark:
            f"<benchmarks>/{name}/calibrate_deviation_penalty.tsv"
        script:
            "../scripts/calibrate_deviation_penalty.py"
