# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Remote solve rules: submit, poll, and collect model solutions from a remote cluster.

Extracted from model.smk for clarity. Depends on names defined in model.smk
(solve_model_inputs, solve_model_runtime, solve_model_mem_mb, solve_model rule)
and common.smk (get_effective_config, get_solver_threads, scenario_override_hash).
All are available because Snakemake `include` shares scope.
"""


def remote_solver_log_path(w):
    """Return canonical remote solve_model log path."""
    return f"logs/{w.name}/solve_model_scen-{w.scenario}.log"


def remote_solver_benchmark_path(w):
    """Return canonical remote solve_model benchmark path."""
    return f"benchmarks/{w.name}/solve_model_scen-{w.scenario}.tsv"


if config["remote_solve"]["enabled"]:
    local_scenarios = list(config["remote_solve"]["local_scenarios"])
    unsupported_local_scenarios = [s for s in local_scenarios if s != "baseline"]
    if unsupported_local_scenarios:
        unsupported = ", ".join(sorted(unsupported_local_scenarios))
        raise ValueError(
            "remote_solve.local_scenarios currently supports only "
            f"['baseline']; unsupported entries: {unsupported}"
        )

    if "baseline" in local_scenarios:

        use rule solve_model as solve_model_local_baseline with:
            output:
                network="<results>/{name}/solved/model_scen-{scenario,baseline}.nc",

        ruleorder: solve_model_local_baseline > collect_remote_solve > solve_model

    else:

        ruleorder: collect_remote_solve > solve_model

    rule sync_remote_workspace:
        output:
            sentinel=temp("<results>/{name}/remote_jobs/.workspace_synced"),
        resources:
            runtime="2m",
            mem_mb=500,
            remote_solves=1,
        log:
            "<logs>/{name}/sync_remote_workspace.log",
        script:
            "../scripts/sync_remote_workspace.py"

    rule submit_remote_solve:
        input:
            unpack(solve_model_inputs),
            workspace="<results>/{name}/remote_jobs/.workspace_synced",
        params:
            solve_runtime=lambda w: get_effective_config(w.scenario)["solving"][
                "runtime"
            ],
            solve_mem_mb=lambda w: get_effective_config(w.scenario)["solving"]["mem_mb"],
            solve_threads=lambda w: get_solver_threads(get_effective_config(w.scenario)),
            # Only used to force correct reruns when scenario definitions change.
            scenario_hash=lambda w: scenario_override_hash(w.scenario),
        output:
            submitted=temp("<results>/{name}/remote_jobs/scen-{scenario}.jobid"),
        resources:
            runtime="2m",
            mem_mb=1200,
            remote_solves=1,  # limit concurrent SSH sessions; use --resources remote_solves=N
        log:
            "<logs>/{name}/submit_remote_solve_scen-{scenario}.log",
        script:
            "../scripts/submit_remote_solve.py"

    rule collect_remote_solve:
        input:
            submitted="<results>/{name}/remote_jobs/scen-{scenario}.jobid",
        params:
            remote_solver_log=remote_solver_log_path,
            remote_solver_benchmark=remote_solver_benchmark_path,
            solve_threads=lambda w: get_solver_threads(get_effective_config(w.scenario)),
        output:
            network="<results>/{name}/solved/model_scen-{scenario}.nc",
        retries: 2
        resources:
            runtime="12h",
            mem_mb=200,
            slurm_runtime=solve_model_runtime,
            slurm_mem_mb=solve_model_mem_mb,
        log:
            "<logs>/{name}/collect_remote_solve_scen-{scenario}.log",
        script:
            "../scripts/collect_remote_solve.py"
