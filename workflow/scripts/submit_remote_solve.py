# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Submit a remote solve_model job via SSH + optional SLURM.

Depends on sync_remote_workspace having already synced workflow code and the
config snapshot. This script only syncs scenario-specific inputs, generates
an sbatch script, and submits it. The SLURM job ID (or "direct") is written
to the output .jobid file for the collect phase.
"""

from pathlib import Path
import shlex

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.remote_solve_utils import (
    build_remote_smk_command,
    config_snapshot_rel_path,
    flatten_input_paths,
    generate_sbatch_script,
    read_remote_config,
    remote_path_shell_expr,
    rsync_ssh_args,
    run_local_command,
    run_ssh_command,
    run_ssh_command_capture,
    to_relative_path,
)


def _submit_remote_solve() -> None:
    logger = setup_script_logging(snakemake.log[0])

    cfg = read_remote_config(snakemake.config)
    project_root = Path.cwd().resolve()
    host = cfg["host"]
    remote_workdir = cfg["workdir"]
    remote_workdir_expr = remote_path_shell_expr(remote_workdir)
    ssh_options = cfg["ssh_options"]
    rsync_options = cfg["rsync_options"]

    config_name = snakemake.wildcards.name
    scenario = snakemake.wildcards.scenario

    # Target is the solved network file that the remote Snakemake will produce.
    target_rel = f"results/{config_name}/solved/model_scen-{scenario}.nc"

    # Config snapshot was written by sync_remote_workspace.
    config_snapshot_rel = config_snapshot_rel_path(config_name)

    # Sync input files (exclude the workspace sentinel).
    sentinel = str(snakemake.input.workspace)
    input_rel_paths = [
        to_relative_path(path, project_root)
        for path in flatten_input_paths(snakemake.input)
        if path != sentinel
    ]
    sync_rel_paths = sorted(set(input_rel_paths))
    for rel_path in sync_rel_paths:
        if not (project_root / rel_path).exists():
            raise FileNotFoundError(f"Missing local input for remote sync: {rel_path}")

    input_sync_command = [
        "rsync",
        "-az",
        "--relative",
        *rsync_ssh_args(ssh_options),
        *rsync_options,
        *sync_rel_paths,
        f"{host}:{remote_workdir.rstrip('/')}/",
    ]
    run_local_command(input_sync_command, logger, cwd=project_root)

    # Build the remote Snakemake command.
    remote_smk_cmd = build_remote_smk_command(
        cfg["pixi_env"], config_snapshot_rel, target_rel
    )

    # Write output .jobid file.
    jobid_path = Path(snakemake.output.submitted)
    jobid_path.parent.mkdir(parents=True, exist_ok=True)

    if cfg["use_slurm"]:
        # Generate and sync sbatch script with base resources.
        sbatch_log_rel = f".snakemake/remote/sbatch_{config_name}_{scenario}.out"
        sbatch_script_rel = f".snakemake/remote/sbatch_{config_name}_{scenario}.sh"
        sbatch_script_path = project_root / sbatch_script_rel

        script_content = generate_sbatch_script(
            job_name=f"solve_{config_name}_{scenario}",
            account=cfg["slurm_account"],
            partition=cfg["slurm_partition"],
            runtime_minutes=int(snakemake.params.solve_runtime),
            mem_mb=int(snakemake.params.solve_mem_mb),
            cpus=int(snakemake.params.solve_threads),
            output_log=sbatch_log_rel,
            smk_command=remote_smk_cmd,
        )
        sbatch_script_path.write_text(script_content, encoding="utf-8")

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

        # Submit without --wait; capture job ID from stdout.
        remote_command = (
            f"cd {remote_workdir_expr} && sbatch {shlex.quote(sbatch_script_rel)}"
        )
        result = run_ssh_command_capture(host, ssh_options, remote_command, logger)
        # sbatch prints "Submitted batch job 12345678"
        job_id = result.stdout.strip().split()[-1]
        logger.info("Submitted SLURM job %s", job_id)
        jobid_path.write_text(job_id, encoding="utf-8")
    else:
        # Direct SSH execution (blocking).
        remote_command = f"cd {remote_workdir_expr} && {remote_smk_cmd}"
        run_ssh_command(host, ssh_options, remote_command, logger)
        jobid_path.write_text("direct", encoding="utf-8")


if __name__ == "__main__":
    _submit_remote_solve()
