# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-shot workspace sync for remote solves.

Runs once per config name before any submit_remote_solve jobs. Creates the
remote workdir, syncs workflow code/config, and writes the config snapshot
(with remote_solve disabled) so that all subsequent submits only need to
sync their scenario-specific inputs.
"""

from pathlib import Path

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.remote_solve_utils import (
    read_remote_config,
    remote_path_shell_expr,
    rsync_ssh_args,
    run_local_command,
    run_ssh_command,
    write_config_snapshot,
)


def _sync_remote_workspace() -> None:
    logger = setup_script_logging(snakemake.log[0])

    cfg = read_remote_config(snakemake.config)
    project_root = Path.cwd().resolve()
    host = cfg["host"]
    remote_workdir = cfg["workdir"]
    remote_workdir_expr = remote_path_shell_expr(remote_workdir)
    ssh_options = cfg["ssh_options"]
    rsync_options = cfg["rsync_options"]

    config_name = snakemake.wildcards.name

    # Preflight: ensure remote workdir exists.
    if cfg["preflight_check"]:
        mkdir_cmd = f"mkdir -p {remote_workdir_expr}"
        run_ssh_command(host, ssh_options, mkdir_cmd, logger)

    # Sync workflow code and config.
    if cfg["sync_workflow"]:
        sync_items = ["workflow", "config", "tools/smk"]
        if cfg["sync_pixi_files"]:
            sync_items.extend(["pixi.toml", "pixi.lock"])
        workflow_sync_command = [
            "rsync",
            "-az",
            *rsync_ssh_args(ssh_options),
            *rsync_options,
            *sync_items,
            f"{host}:{remote_workdir.rstrip('/')}/",
        ]
        run_local_command(workflow_sync_command, logger, cwd=project_root)

    # Write config snapshot with remote_solve disabled and sync it.
    config_snapshot_rel = write_config_snapshot(
        snakemake.config, config_name, project_root
    )
    snapshot_sync_command = [
        "rsync",
        "-az",
        "--relative",
        *rsync_ssh_args(ssh_options),
        *rsync_options,
        config_snapshot_rel,
        f"{host}:{remote_workdir.rstrip('/')}/",
    ]
    run_local_command(snapshot_sync_command, logger, cwd=project_root)

    # Touch sentinel output.
    sentinel = Path(snakemake.output.sentinel)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()


if __name__ == "__main__":
    _sync_remote_workspace()
