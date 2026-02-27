# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collect a remote solve_model result: poll SLURM, retry on failure, pull artifacts.

This lightweight rule reads the job ID written by ``submit_remote_solve``,
polls the SLURM job until completion, and pulls the solved network back.

On SLURM failure (OOM, timeout), it re-submits with attempt-scaled resources
(provided by Snakemake's ``retries`` mechanism via ``slurm_runtime`` and
``slurm_mem_mb`` resources) and polls the new job.

On local interrupt (Ctrl-C / SIGTERM), the remote SLURM job is cancelled
and the .jobid file is removed so the next run starts fresh.
"""

from pathlib import Path
import shlex
import signal
import subprocess
import time

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.remote_solve_utils import (
    build_remote_smk_command,
    config_snapshot_rel_path,
    daemon_paths,
    generate_sbatch_script,
    pull_artifact,
    read_job_status_from_cache,
    read_remote_config,
    remote_path_shell_expr,
    rsync_ssh_args,
    run_local_command,
    run_ssh_command_capture,
    start_daemon_if_needed,
    to_relative_path,
)

# Terminal SLURM states that indicate the job will not recover.
_SLURM_FAILED_STATES = frozenset(
    {
        "BOOT_FAIL",
        "CANCELLED",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "TIMEOUT",
    }
)

_POLL_INTERVAL_SECONDS = 10

# Timeout for the scancel SSH call during cleanup (seconds).
_CANCEL_TIMEOUT_SECONDS = 30


def _check_job_status(host, ssh_options, job_id, logger):
    """Check SLURM job status. Returns (state_str, is_terminal, succeeded).

    Uses squeue first (for running jobs), falls back to sacct (for completed).
    Returns (None, False, False) on transient SSH failure.
    """
    # Try squeue first.
    squeue_cmd = f"squeue -j {shlex.quote(job_id)} -h -o '%T'"
    result = run_ssh_command_capture(host, ssh_options, squeue_cmd, logger, check=False)
    if result.returncode == 0:
        state = result.stdout.strip()
        if state:
            return state, False, False  # Job still active.
        # Empty output means job left the queue; check sacct.
    else:
        # squeue failure could be transient or job already gone; try sacct.
        logger.debug("squeue failed (rc=%d), trying sacct", result.returncode)

    # Check sacct for final state.
    sacct_cmd = (
        f"sacct -j {shlex.quote(job_id)} --format=State --noheader --parsable2"
        " | head -1"
    )
    result = run_ssh_command_capture(host, ssh_options, sacct_cmd, logger, check=False)
    if result.returncode != 0:
        logger.warning("sacct failed (rc=%d); treating as transient", result.returncode)
        return None, False, False

    state = result.stdout.strip().split("\n")[0].strip()
    if not state:
        logger.warning("Empty sacct output for job %s; treating as transient", job_id)
        return None, False, False

    if state == "COMPLETED":
        return state, True, True
    if state in _SLURM_FAILED_STATES:
        return state, True, False
    # Other states (RUNNING shown by sacct, etc.)
    return state, False, False


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
    run_local_command(sbatch_sync_command, logger, cwd=project_root)

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


def _cancel_remote_job(host, ssh_options, job_id, jobid_path, logger):
    """Best-effort cancel of a remote SLURM job and cleanup of the jobid file."""
    # Cancel the remote job.
    try:
        cmd = ["ssh", *ssh_options, host, f"scancel {shlex.quote(job_id)}"]
        logger.info("Cancelling remote SLURM job %s", job_id)
        subprocess.run(
            cmd,
            timeout=_CANCEL_TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        logger.warning("Could not cancel remote job %s", job_id, exc_info=True)

    # Remove the jobid file so the next run submits fresh.
    try:
        if jobid_path.exists():
            jobid_path.unlink()
            logger.info("Removed jobid file %s", jobid_path)
    except Exception:
        logger.warning("Could not remove jobid file %s", jobid_path, exc_info=True)


def _collect_remote_solve() -> None:
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
            # Start the batch polling daemon (no-op if already running).
            start_daemon_if_needed(cfg, jobid_dir, logger)

            # Check if the job from the submit phase has already failed
            # (indicating this is a retry attempt).
            state, is_terminal, succeeded = _check_job_status(
                host, ssh_options, job_id, logger
            )
            if is_terminal and not succeeded:
                logger.warning(
                    "Job %s already in terminal failed state (%s); "
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

            # Poll until completion.
            while True:
                # Try daemon cache first (cheap local JSON read).
                cached = read_job_status_from_cache(job_id, cache_file)
                if cached is not None:
                    state, is_terminal, succeeded = cached
                else:
                    # Cache miss/stale: fall back to direct SSH.
                    state, is_terminal, succeeded = _check_job_status(
                        host, ssh_options, job_id, logger
                    )

                if state is None:
                    # Transient failure; wait and retry.
                    logger.info(
                        "Transient check failure; retrying in %ds",
                        _POLL_INTERVAL_SECONDS,
                    )
                    time.sleep(_POLL_INTERVAL_SECONDS)
                    continue

                if is_terminal:
                    if succeeded:
                        logger.info("Job %s completed successfully", job_id)
                        slurm_job_done = True
                        break
                    else:
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

        # Pull artifacts.
        pull_artifact(
            host=host,
            remote_workdir=remote_workdir,
            rel_path=target_rel,
            local_root=project_root,
            rsync_options=rsync_options,
            ssh_options=ssh_options,
            logger=logger,
            required=True,
        )
        pull_artifact(
            host=host,
            remote_workdir=remote_workdir,
            rel_path=remote_solver_log_rel,
            local_root=project_root,
            rsync_options=rsync_options,
            ssh_options=ssh_options,
            logger=logger,
            required=False,
        )
        pull_artifact(
            host=host,
            remote_workdir=remote_workdir,
            rel_path=remote_solver_benchmark_rel,
            local_root=project_root,
            rsync_options=rsync_options,
            ssh_options=ssh_options,
            logger=logger,
            required=False,
        )
    except (KeyboardInterrupt, SystemExit):
        if not slurm_job_done:
            _cancel_remote_job(host, ssh_options, job_id, jobid_path, logger)
        raise


if __name__ == "__main__":
    _collect_remote_solve()
