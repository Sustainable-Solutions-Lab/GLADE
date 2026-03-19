# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-shot workspace sync for remote solves.

Runs once per config name before any submit_remote_solve jobs. Creates the
remote workdir, syncs the config snapshot (with remote_solve disabled) so
that all subsequent submits only need to sync their scenario-specific inputs.

When ``sync_workflow`` is disabled (the default), a git commit check verifies
that the remote repository contains all local commits and logs a warning if
not. When ``sync_workflow`` is enabled, workflow code and config are rsynced
to the remote (which may dirty the remote's git state).
"""

from pathlib import Path
import subprocess

from workflow.scripts.logging_config import setup_script_logging
from workflow.scripts.remote_solve_utils import (
    check_ssh_master,
    read_remote_config,
    remote_path_shell_expr,
    rsync_ssh_args,
    run_rsync_push,
    run_ssh_command,
    run_ssh_command_capture,
    write_config_snapshot,
)


def _check_git_compatibility(
    host: str,
    ssh_options: list[str],
    remote_workdir_expr: str,
    logger,
) -> None:
    """Warn if the remote repo is missing commits from the local HEAD.

    Gets the local HEAD hash, then asks the remote whether that commit is
    reachable from its current HEAD via ``git merge-base --is-ancestor``.
    Any failure (no git, network hiccup) is logged but never fatal.
    """
    try:
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.debug("Could not determine local git HEAD; skipping compatibility check")
        return

    git_prefix = f"cd {remote_workdir_expr} && git"
    try:
        remote_head = run_ssh_command_capture(
            host,
            ssh_options,
            f"{git_prefix} rev-parse HEAD",
            logger,
            retries=1,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.debug(
            "Could not determine remote git HEAD; skipping compatibility check"
        )
        return

    if local_head == remote_head:
        logger.info("Local and remote HEAD match (%s)", local_head[:10])
        return

    # Check if local HEAD is an ancestor of the remote HEAD (remote has all
    # our commits).  git merge-base --is-ancestor exits 0 if yes, 1 if no.
    try:
        result = run_ssh_command_capture(
            host,
            ssh_options,
            f"{git_prefix} merge-base --is-ancestor {local_head} HEAD",
            logger,
            check=False,
            retries=1,
        )
        if result.returncode != 0:
            logger.warning(
                "Remote repository does not contain local HEAD %s. "
                "The remote code may be out of date — consider pushing your "
                "changes and updating the remote branch.",
                local_head[:10],
            )
        else:
            logger.info(
                "Remote HEAD (%s) contains local HEAD (%s); code is compatible",
                remote_head[:10],
                local_head[:10],
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.debug("Git ancestry check failed; skipping compatibility check")


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

    # Verify SSH master is healthy before starting any transfers.
    check_ssh_master(host, ssh_options, logger)

    # Preflight: ensure remote workdir exists.
    if cfg["preflight_check"]:
        mkdir_cmd = f"mkdir -p {remote_workdir_expr}"
        run_ssh_command(host, ssh_options, mkdir_cmd, logger)

    # Sync workflow code, or check git compatibility.
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
        run_rsync_push(workflow_sync_command, logger, cwd=project_root)
    else:
        _check_git_compatibility(host, ssh_options, remote_workdir_expr, logger)

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
    run_rsync_push(snapshot_sync_command, logger, cwd=project_root)

    # Touch sentinel output.
    sentinel = Path(snakemake.output.sentinel)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()


if __name__ == "__main__":
    _sync_remote_workspace()
