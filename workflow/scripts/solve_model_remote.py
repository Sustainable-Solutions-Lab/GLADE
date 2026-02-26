# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run solve_model remotely via SSH + Snakemake/SLURM.

This wrapper keeps model building local and delegates only solve_model to a
remote cluster. It syncs required inputs and workflow/config code, runs a
remote Snakemake target constrained to solve_model, then pulls solved artifacts
back to the local workspace.
"""

import copy
from pathlib import Path
import shlex
import subprocess

import yaml

from workflow.scripts.logging_config import setup_script_logging


def _run_local_command(
    command: list[str], logger, *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a local subprocess command and log it."""
    logger.info("$ %s", shlex.join(command))
    return subprocess.run(
        command,
        check=check,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
    )


def _run_ssh_command(
    host: str,
    ssh_options: list[str],
    remote_command: str,
    logger,
) -> None:
    """Run a remote shell command over SSH."""
    command = ["ssh", *ssh_options, host, remote_command]
    _run_local_command(command, logger)


def _remote_path_shell_expr(path: str) -> str:
    """Build a shell-safe remote path expression with home expansion support."""
    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        suffix = path[2:].replace('"', '\\"')
        return f'"$HOME/{suffix}"'
    return shlex.quote(path)


def _to_relative_path(path: str, project_root: Path) -> str:
    """Return project-relative POSIX path, failing for external paths."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Path '{resolved}' is outside the project root '{project_root}'. "
            "remote_solve currently supports only in-repo paths."
        ) from exc


def _flatten_input_paths(snakemake_input) -> list[str]:
    """Flatten Snakemake input values (string or list-like) to paths."""
    paths: list[str] = []
    for _, value in snakemake_input.items():
        if isinstance(value, str):
            paths.append(value)
            continue
        paths.extend(str(item) for item in value)
    return paths


def _pull_artifact(
    *,
    host: str,
    remote_workdir: str,
    rel_path: str,
    local_root: Path,
    rsync_options: list[str],
    logger,
    required: bool,
) -> None:
    """Pull one artifact from remote to local."""
    local_path = local_root / rel_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path = f"{host}:{remote_workdir.rstrip('/')}/{rel_path}"
    command = ["rsync", "-az", *rsync_options, remote_path, str(local_path)]

    try:
        _run_local_command(command, logger)
    except subprocess.CalledProcessError:
        if required:
            raise
        logger.warning("Remote artifact missing (optional): %s", rel_path)


def _run_remote_solve() -> None:
    logger = setup_script_logging(snakemake.log[0])

    cfg = snakemake.config["remote_solve"]
    if not cfg["enabled"]:
        raise ValueError(
            "remote_solve.enabled is false; solve_model_remote should not be selected."
        )

    project_root = Path.cwd().resolve()
    host = str(cfg["host"])
    remote_workdir = str(cfg["workdir"])
    remote_workdir_expr = _remote_path_shell_expr(remote_workdir)
    remote_env = str(cfg["pixi_env"])
    use_slurm = bool(cfg["use_slurm"])
    slurm_profile = str(cfg["slurm_profile"])
    sync_workflow = bool(cfg["sync_workflow"])
    sync_pixi_files = bool(cfg["sync_pixi_files"])
    preflight_check = bool(cfg["preflight_check"])
    ssh_options = [str(option) for option in cfg["ssh_options"]]
    rsync_options = [str(option) for option in cfg["rsync_options"]]

    target_rel = _to_relative_path(snakemake.output.network, project_root)
    remote_solver_log_rel = _to_relative_path(
        snakemake.params.remote_solver_log, project_root
    )
    remote_solver_benchmark_rel = _to_relative_path(
        snakemake.params.remote_solver_benchmark, project_root
    )

    if preflight_check:
        mkdir_cmd = f"mkdir -p {remote_workdir_expr}"
        _run_ssh_command(host, ssh_options, mkdir_cmd, logger)

    if sync_workflow:
        sync_items = ["workflow", "config", "tools/smk"]
        if sync_pixi_files:
            sync_items.extend(["pixi.toml", "pixi.lock"])
        workflow_sync_command = [
            "rsync",
            "-az",
            *rsync_options,
            *sync_items,
            f"{host}:{remote_workdir.rstrip('/')}/",
        ]
        _run_local_command(workflow_sync_command, logger, cwd=project_root)

    # Write an exact local config snapshot for the remote solve and force
    # remote_solve disabled remotely to avoid recursive SSH delegation.
    config_snapshot_rel = (
        ".snakemake/remote/"
        f"config_remote_solve_{snakemake.wildcards.name}_{snakemake.wildcards.scenario}.yaml"
    )
    config_snapshot_path = project_root / config_snapshot_rel
    config_snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    remote_config = copy.deepcopy(dict(snakemake.config))
    remote_config["remote_solve"]["enabled"] = False
    with config_snapshot_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(remote_config, handle, sort_keys=False)

    input_rel_paths = [
        _to_relative_path(path, project_root)
        for path in _flatten_input_paths(snakemake.input)
    ]
    sync_rel_paths = sorted({config_snapshot_rel, *input_rel_paths})
    for rel_path in sync_rel_paths:
        if not (project_root / rel_path).exists():
            raise FileNotFoundError(f"Missing local input for remote sync: {rel_path}")

    input_sync_command = [
        "rsync",
        "-az",
        "--relative",
        *rsync_options,
        *sync_rel_paths,
        f"{host}:{remote_workdir.rstrip('/')}/",
    ]
    _run_local_command(input_sync_command, logger, cwd=project_root)

    remote_smk_cmd = ["tools/smk", "-e", remote_env]
    if use_slurm:
        remote_smk_cmd.append("--slurm")
    remote_smk_cmd.extend(
        [
            "--configfile",
            config_snapshot_rel,
            "--allowed-rules",
            "solve_model",
            "-j1",
            target_rel,
        ]
    )

    remote_smk_text = shlex.join(remote_smk_cmd)
    if slurm_profile:
        remote_smk_text = (
            f"SMK_SLURM_PROFILE={shlex.quote(slurm_profile)} " f"{remote_smk_text}"
        )
    remote_command = f"cd {remote_workdir_expr} && {remote_smk_text}"
    _run_ssh_command(host, ssh_options, remote_command, logger)

    _pull_artifact(
        host=host,
        remote_workdir=remote_workdir,
        rel_path=target_rel,
        local_root=project_root,
        rsync_options=rsync_options,
        logger=logger,
        required=True,
    )
    _pull_artifact(
        host=host,
        remote_workdir=remote_workdir,
        rel_path=remote_solver_log_rel,
        local_root=project_root,
        rsync_options=rsync_options,
        logger=logger,
        required=False,
    )
    _pull_artifact(
        host=host,
        remote_workdir=remote_workdir,
        rel_path=remote_solver_benchmark_rel,
        local_root=project_root,
        rsync_options=rsync_options,
        logger=logger,
        required=False,
    )


if __name__ == "__main__":
    _run_remote_solve()
