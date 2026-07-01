# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Iterative calibration of L1 deviation-penalty coefficients.

Drives the per-component deviation percentages (any non-empty subset of
{cropland, grassland, feed, diet}) simultaneously to the target percentage via Broyden's
method in log-log coordinates. Each iteration is one paired baseline+main
solve, run in-process by the script.
"""


_dp_cal_cfg = config["deviation_penalty"]["calibration"]

if _dp_cal_cfg["generate"]:
    _trace_csv = _dp_cal_cfg["trace_csv"].format(name=name)

    rule calibrate_deviation_penalty:
        input:
            # Health inputs only when health is enabled (the solve prices health
            # only then); omitted otherwise so no IHME GBD data is required.
            unpack(lambda w: health_input_paths(name) if health_required() else {}),
            model=f"<results>/{name}/build/model.nc",
            baseline_diet=f"<processing>/{name}/baseline_diet.csv",
            m49="data/curated/M49-codes.csv",
            food_groups="data/curated/food_groups.csv",
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
            # Warm-start seed: a side copy of the previous calibrated yaml,
            # written by the script next to the trace. It must not be the
            # calibrated_yaml output itself (Snakemake deletes outputs before
            # the job runs, so that warm start would never engage) nor a
            # declared input/output (self-loop / same deletion). Pathvars are
            # resolved explicitly since params are not path-expanded. The
            # script loads it iff the file exists on disk at run time.
            previous_yaml=str(
                Path(resolve_pathvars(_trace_csv, PATH_ROOTS)).with_name(
                    "deviation_penalty_warm.yaml"
                )
            ),
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
