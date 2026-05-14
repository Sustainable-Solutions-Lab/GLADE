# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rule for calibrating L1 production-stability penalty costs.

Drives the land-use deviation and animal-feed deviation simultaneously to
the target percentage via Broyden's method in log-log coordinates. Each
iteration is one paired baseline+main solve, run in-process by the script.
"""


_prod_stability_cal_cfg = config["prod_stability_calibration"]

if _prod_stability_cal_cfg["generate"]:
    _trace_csv = _prod_stability_cal_cfg["trace_csv"].format(name=name)

    rule calibrate_prod_stability:
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
            health_derived_tmrel=f"<processing>/{name}/health/derived_tmrel.csv",
            health_cluster_risk_baseline=f"<processing>/{name}/health/cluster_risk_baseline.csv",
            nutrition="data/curated/nutrition.csv",
        output:
            calibrated_l1=_prod_stability_cal_cfg["calibrated_l1_yaml"],
            trace=_trace_csv,
        params:
            target_pct=_prod_stability_cal_cfg["target_deviation_pct"],
            seed_land_l1_cost=_prod_stability_cal_cfg["seed_land_l1_cost"],
            seed_animal_feed_l1_cost=_prod_stability_cal_cfg["seed_animal_feed_l1_cost"],
            tolerance=_prod_stability_cal_cfg["tolerance"],
            max_iter=_prod_stability_cal_cfg["max_iter"],
            trust_region_log=_prod_stability_cal_cfg["trust_region_log"],
            # Warm-start path is passed as a param (not an input) so Snakemake
            # doesn't create a self-loop on calibrated_l1_yaml. The script
            # loads it iff the file exists on disk at run time.
            previous_yaml=_prod_stability_cal_cfg["calibrated_l1_yaml"],
            name=name,
        resources:
            # Per-iteration solves use mem_mb / runtime configured in the
            # solving section. Calibration itself is light; the bulk of
            # memory goes into the in-process pypsa.Network objects.
            runtime=lambda w, attempt: 60 * 60 * (1 + attempt),
            mem_mb=lambda w, attempt: 12000 * attempt,
        log:
            f"<logs>/{name}/calibrate_prod_stability.log",
        benchmark:
            f"<benchmarks>/{name}/calibrate_prod_stability.tsv"
        script:
            "../scripts/calibrate_prod_stability.py"
