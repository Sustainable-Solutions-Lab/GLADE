# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collect a remote solve_model result: poll SLURM, retry on failure, pull artifacts.

This lightweight rule reads the job ID written by ``submit_remote_solve``,
waits for job completion via the batch polling daemon's cache, and pulls
the solved network back.

On SLURM failure (OOM, timeout), it re-submits with attempt-scaled resources
(provided by Snakemake's ``retries`` mechanism via ``slurm_runtime`` and
``slurm_mem_mb`` resources) and polls the new job.

On local interrupt (Ctrl-C / SIGTERM), the poll daemon is signalled to
cancel all tracked SLURM jobs via both a shutdown marker file (visible to
all daemon instances) and SIGTERM. The .jobid files are preserved (they
are ``temp()`` in Snakemake); on re-run the collect script picks up the
existing file and handles the CANCELLED state.
"""

import contextlib
import fcntl
from pathlib import Path
import shlex
import signal
import subprocess
import time
import traceback

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.remote_solve_utils import (
    build_remote_smk_command,
    check_ssh_master,
    config_snapshot_rel_path,
    daemon_paths,
    generate_sbatch_script,
    is_daemon_running,
    pull_artifacts,
    read_job_status_from_cache,
    read_remote_config,
    remote_path_shell_expr,
    rsync_ssh_args,
    run_rsync_push,
    run_ssh_command_capture,
    signal_daemon_shutdown,
    start_daemon_if_needed,
    to_relative_path,
    write_daemon_shutdown_marker,
)

_POLL_INTERVAL_SECONDS = 10


def _resubmit_with_scaled_resources(cfg, snakemake, project_root, logger):
    """Re-submit an sbatch job with attempt-scaled resources. Returns new job ID."""
    host = cfg["host"]
    remote_workdir = cfg["workdir"]
    remote_workdir_expr = remote_path_shell_expr(remote_workdir)
    ssh_options = cfg["ssh_options"]
    rsync_options = cfg["rsync_options"]

    config_name = snakemake.wildcards.name
    scenario = snakemake.wildcards.scenario

    target_rel = f"results/{config_name}/solved/model_scen-{scenario}.nc"
    config_snapshot_rel = config_snapshot_rel_path(config_name)

    remote_smk_cmd = build_remote_smk_command(
        cfg["pixi_env"], config_snapshot_rel, target_rel
    )

    # Use attempt-scaled resources from Snakemake.
    scaled_runtime = int(snakemake.resources.slurm_runtime)
    scaled_mem_mb = int(snakemake.resources.slurm_mem_mb)
    solve_threads = int(snakemake.params.solve_threads)

    logger.info(
        "Re-submitting with scaled resources: runtime=%dm, mem=%dMB, cpus=%d",
        scaled_runtime,
        scaled_mem_mb,
        solve_threads,
    )

    sbatch_log_rel = f".snakemake/remote/sbatch_{config_name}_{scenario}.out"
    sbatch_script_rel = f".snakemake/remote/sbatch_{config_name}_{scenario}.sh"
    sbatch_script_path = project_root / sbatch_script_rel

    script_content = generate_sbatch_script(
        job_name=f"solve_{config_name}_{scenario}",
        account=cfg["slurm_account"],
        partition=cfg["slurm_partition"],
        runtime_minutes=scaled_runtime,
        mem_mb=scaled_mem_mb,
        cpus=solve_threads,
        output_log=sbatch_log_rel,
        smk_command=remote_smk_cmd,
    )
    sbatch_script_path.write_text(script_content, encoding="utf-8")

    # Sync updated sbatch script.
    sbatch_sync_command = [
        "rsync",
        "-az",
        "--relative",
        *rsync_ssh_args(ssh_options),
        *rsync_options,
        sbatch_script_rel,
        f"{host}:{remote_workdir.rstrip('/')}/",
    ]
    run_rsync_push(sbatch_sync_command, logger, cwd=project_root)

    # Submit without --wait.
    remote_command = (
        f"cd {remote_workdir_expr} && sbatch {shlex.quote(sbatch_script_rel)}"
    )
    result = run_ssh_command_capture(host, ssh_options, remote_command, logger)
    new_job_id = result.stdout.strip().split()[-1]
    logger.info("Re-submitted SLURM job %s", new_job_id)

    # Update the .jobid file.
    jobid_path = Path(snakemake.input.submitted)
    jobid_path.write_text(new_job_id, encoding="utf-8")

    return new_job_id


def _pull_sbatch_log_for_diagnostics(cfg, config_name, scenario, project_root, logger):
    """Try to pull the sbatch output log for failure diagnostics."""
    sbatch_log_rel = f".snakemake/remote/sbatch_{config_name}_{scenario}.out"
    pull_artifact(
        host=cfg["host"],
        remote_workdir=cfg["workdir"],
        rel_path=sbatch_log_rel,
        local_root=project_root,
        rsync_options=cfg["rsync_options"],
        ssh_options=cfg["ssh_options"],
        logger=logger,
        required=False,
    )
    sbatch_log_local = project_root / sbatch_log_rel
    if sbatch_log_local.exists():
        logger.error(
            "sbatch output log:\n%s",
            sbatch_log_local.read_text(encoding="utf-8", errors="replace"),
        )


def _collect_remote_solve() -> None:
    """Top-level entry point with crash-safe logging wrapper."""
    log_path = snakemake.log[0]
    try:
        _collect_remote_solve_inner()
    except BaseException:
        # Ensure any crash traceback reaches the log file for diagnostics.
        # (Without this, unhandled exceptions only go to stderr, which
        # Snakemake doesn't capture to the per-rule log file.)
        with contextlib.suppress(OSError), open(log_path, "a") as f:
            f.write(f"\n--- CRASH ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
            traceback.print_exc(file=f)
        raise


def _collect_remote_solve_inner() -> None:
    # Make SIGTERM (sent by Snakemake on interrupt) raise SystemExit so our
    # except clause can catch it and run cleanup.
    signal.signal(
        signal.SIGTERM,
        lambda signum, frame: (_ for _ in ()).throw(SystemExit(128 + signum)),
    )

    logger = setup_script_logging(snakemake.log[0])

    cfg = read_remote_config(snakemake.config)
    project_root = Path.cwd().resolve()
    host = cfg["host"]
    remote_workdir = cfg["workdir"]
    ssh_options = cfg["ssh_options"]
    rsync_options = cfg["rsync_options"]

    config_name = snakemake.wildcards.name
    scenario = snakemake.wildcards.scenario

    # Read job ID from submit phase.
    jobid_path = Path(snakemake.input.submitted)
    job_id = jobid_path.read_text(encoding="utf-8").strip()
    logger.info("Collecting remote solve for job: %s", job_id)

    target_rel = to_relative_path(snakemake.output.network, project_root)
    remote_solver_log_rel = to_relative_path(
        snakemake.params.remote_solver_log, project_root
    )
    remote_solver_benchmark_rel = to_relative_path(
        snakemake.params.remote_solver_benchmark, project_root
    )

    # Track whether the SLURM job completed successfully. If we're
    # interrupted before that, we cancel the remote job and clean up.
    slurm_job_done = not cfg["use_slurm"] or job_id == "direct"

    # Resolve the jobid directory and daemon cache path for batch polling.
    jobid_dir = jobid_path.parent
    paths = daemon_paths(jobid_dir)
    cache_file = paths["cache_file"]

    try:
        if not slurm_job_done:
            # Verify SSH master is healthy before starting long-lived polling.
            check_ssh_master(host, ssh_options, logger)

            # Start the batch polling daemon (no-op if already running).
            start_daemon_if_needed(cfg, jobid_dir, logger)

            # Track whether we've already re-submitted once in this
            # collect attempt (to avoid infinite resubmit loops).
            resubmitted = False

            # Poll exclusively via daemon cache.
            logger.info("Entering daemon-cache poll loop for job %s", job_id)
            while True:
                cached = read_job_status_from_cache(job_id, cache_file)
                if cached is None:
                    # Cache miss or stale — ensure daemon is alive.
                    if not is_daemon_running(jobid_dir):
                        logger.warning("Poll daemon not running; restarting")
                        start_daemon_if_needed(cfg, jobid_dir, logger)
                    time.sleep(_POLL_INTERVAL_SECONDS)
                    continue

                state, is_terminal, succeeded = cached

                if is_terminal:
                    if succeeded:
                        logger.info("Job %s completed successfully", job_id)
                        slurm_job_done = True
                        break

                    if not resubmitted:
                        logger.warning(
                            "Job %s failed with state %s; "
                            "re-submitting with scaled resources",
                            job_id,
                            state,
                        )
                        _pull_sbatch_log_for_diagnostics(
                            cfg, config_name, scenario, project_root, logger
                        )
                        job_id = _resubmit_with_scaled_resources(
                            cfg, snakemake, project_root, logger
                        )
                        resubmitted = True
                        time.sleep(_POLL_INTERVAL_SECONDS)
                        continue

                    logger.error("Job %s failed with state: %s", job_id, state)
                    _pull_sbatch_log_for_diagnostics(
                        cfg, config_name, scenario, project_root, logger
                    )
                    raise subprocess.CalledProcessError(
                        1,
                        f"SLURM job {job_id}",
                        stderr=f"Job ended in state: {state}",
                    )

                logger.info(
                    "Job %s state: %s; polling in %ds",
                    job_id,
                    state,
                    _POLL_INTERVAL_SECONDS,
                )
                time.sleep(_POLL_INTERVAL_SECONDS)

        # Pull artifacts in a single rsync call, serialized across collect
        # instances via file lock to avoid overwhelming the SSH connection
        # when many jobs complete simultaneously (e.g. after a workflow restart).
        pull_lock = jobid_dir / ".pull_artifacts.lock"
        logger.info("Waiting for pull lock")
        with open(pull_lock, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            logger.info("Pulling artifacts")
            pull_artifacts(
                host=host,
                remote_workdir=remote_workdir,
                rel_paths=[
                    target_rel,
                    remote_solver_log_rel,
                    remote_solver_benchmark_rel,
                ],
                required_paths=[target_rel],
                local_root=project_root,
                rsync_options=rsync_options,
                ssh_options=ssh_options,
                logger=logger,
            )
    except (KeyboardInterrupt, SystemExit):
        if not slurm_job_done:
            write_daemon_shutdown_marker(jobid_dir)
            signal_daemon_shutdown(jobid_dir, logger)
        raise


if __name__ == "__main__":
    _collect_remote_solve()
