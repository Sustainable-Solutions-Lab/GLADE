# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for remote solve submission and collection.

Used by ``submit_remote_solve.py``, ``collect_remote_solve.py``, and
``poll_remote_jobs.py``.
"""

import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import yaml


def run_local_command(
    command: list[str],
    logger,
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run a local subprocess command and log it."""
    logger.info("$ %s", shlex.join(command))
    return subprocess.run(
        command,
        check=check,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
    )


def _retry_command(
    command_fn,
    *,
    retries: int,
    delay: float,
    logger,
    description: str,
):
    """Generic retry wrapper for SSH/rsync operations.

    Retries on ``TimeoutExpired`` or ``CalledProcessError`` with rc=255
    (SSH connection failure). Other errors propagate immediately.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return command_fn()
        except subprocess.TimeoutExpired as exc:
            last_exc = exc
            logger.warning(
                "%s timed out (attempt %d/%d)", description, attempt, retries
            )
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 255:
                last_exc = exc
                logger.warning(
                    "%s SSH connection failed (attempt %d/%d)",
                    description,
                    attempt,
                    retries,
                )
            else:
                raise
        if attempt < retries:
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def run_ssh_command(
    host: str,
    ssh_options: list[str],
    remote_command: str,
    logger,
    *,
    timeout: float | None = 60,
    retries: int = 3,
) -> None:
    """Run a remote shell command over SSH (fire-and-forget), with retries."""
    command = ["ssh", *ssh_options, host, remote_command]

    def _run():
        run_local_command(command, logger, timeout=timeout)

    _retry_command(
        _run,
        retries=retries,
        delay=5,
        logger=logger,
        description=f"SSH command to {host}",
    )


def run_ssh_command_capture(
    host: str,
    ssh_options: list[str],
    remote_command: str,
    logger,
    *,
    check: bool = True,
    timeout: float | None = 60,
    retries: int = 3,
) -> subprocess.CompletedProcess:
    """Run a remote shell command over SSH and capture stdout, with retries.

    When ``check=False`` (e.g. squeue/sacct calls), only ``TimeoutExpired``
    triggers retry since no ``CalledProcessError`` is raised.
    """
    command = ["ssh", *ssh_options, host, remote_command]

    def _run():
        return run_local_command(
            command, logger, capture_output=True, check=check, timeout=timeout
        )

    return _retry_command(
        _run,
        retries=retries,
        delay=5,
        logger=logger,
        description=f"SSH capture to {host}",
    )


def run_rsync_push(
    command: list[str],
    logger,
    *,
    cwd: Path | None = None,
    timeout: float = 120,
    retries: int = 3,
) -> None:
    """Run an rsync push command with timeout and retries."""

    def _run():
        run_local_command(command, logger, cwd=cwd, timeout=timeout)

    _retry_command(
        _run,
        retries=retries,
        delay=5,
        logger=logger,
        description="rsync push",
    )


def remote_path_shell_expr(path: str) -> str:
    """Build a shell-safe remote path expression with home expansion support."""
    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        suffix = path[2:].replace('"', '\\"')
        return f'"$HOME/{suffix}"'
    return shlex.quote(path)


def to_relative_path(path: str, project_root: Path) -> str:
    """Return project-relative POSIX path, failing for external paths."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Path '{resolved}' is outside the project root '{project_root}'. "
            "remote_solve currently supports only in-repo paths."
        ) from exc


def flatten_input_paths(snakemake_input) -> list[str]:
    """Flatten Snakemake input values (string or list-like) to paths."""
    paths: list[str] = []
    for _, value in snakemake_input.items():
        if isinstance(value, str):
            paths.append(value)
            continue
        paths.extend(str(item) for item in value)
    return paths


def pull_artifact(
    *,
    host: str,
    remote_workdir: str,
    rel_path: str,
    local_root: Path,
    rsync_options: list[str],
    ssh_options: list[str],
    logger,
    required: bool,
) -> None:
    """Pull one artifact from remote to local, with timeout and retries."""
    local_path = local_root / rel_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path = f"{host}:{remote_workdir.rstrip('/')}/{rel_path}"
    command = [
        "rsync",
        "-az",
        "--timeout=60",
        *rsync_ssh_args(ssh_options),
        *rsync_options,
        remote_path,
        str(local_path),
    ]

    def _run():
        run_local_command(command, logger, timeout=120)

    try:
        _retry_command(
            _run,
            retries=3,
            delay=5,
            logger=logger,
            description=f"pull {rel_path}",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if required:
            raise
        logger.warning("Remote artifact missing (optional): %s", rel_path)


def generate_sbatch_script(
    *,
    job_name: str,
    account: str,
    partition: str,
    runtime_minutes: int,
    mem_mb: int,
    cpus: int,
    output_log: str,
    smk_command: str,
) -> str:
    """Return an sbatch submission script as a string."""
    import textwrap

    hours, mins = divmod(runtime_minutes, 60)
    time_str = f"{hours}:{mins:02d}:00"
    return textwrap.dedent(f"""\
        #!/bin/bash
        #SBATCH --job-name={job_name}
        #SBATCH --account={account}
        #SBATCH --partition={partition}
        #SBATCH --time={time_str}
        #SBATCH --mem={mem_mb}M
        #SBATCH --cpus-per-task={cpus}
        #SBATCH --output={output_log}

        {smk_command}
    """)


def build_remote_smk_command(
    pixi_env: str, config_snapshot_rel: str, target_rel: str
) -> str:
    """Build the remote tools/smk command string for solve_model."""
    return shlex.join(
        [
            "tools/smk",
            "-e",
            pixi_env,
            "--configfile",
            config_snapshot_rel,
            "--allowed-rules",
            "solve_model",
            "--nolock",
            "-j1",
            target_rel,
        ]
    )


# Default SSH keepalive options: detect dead connections within ~45s
# (3 probes x 15s interval) instead of hanging indefinitely.
# NOTE: These only take full effect when ControlMaster is not used, or when
# the master itself has ServerAliveInterval set in ~/.ssh/config. With a
# ControlMaster, slave keepalives only probe the local master process.
# To compensate, check_ssh_master() validates the master before operations.
_DEFAULT_SSH_KEEPALIVE = [
    "-o",
    "ConnectTimeout=30",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
]


def check_ssh_master(host: str, ssh_options: list[str], logger) -> None:
    """Verify the SSH ControlMaster connection is usable; fail fast if stale.

    When ``ControlMaster auto`` is configured in ``~/.ssh/config``, a dead
    master socket causes all multiplexed connections to hang silently.
    This sends a quick ``ssh -O check`` + probe and raises an error if the
    master exists but the connection is broken, so the user can
    re-authenticate (which may require 2FA) before retrying.
    """
    # Step 1: Check if a ControlMaster socket exists.
    try:
        check = subprocess.run(
            ["ssh", *ssh_options, "-O", "check", host],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        logger.warning("SSH master check timed out for %s; probing anyway", host)
    else:
        if check.returncode != 0:
            # No master running — SSH will create one on demand.
            return

    # Step 2: Master exists (or check hung) — probe actual connectivity.
    try:
        probe = subprocess.run(
            ["ssh", *ssh_options, host, "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return
        logger.warning("SSH probe failed (rc=%d) for %s", probe.returncode, host)
    except subprocess.TimeoutExpired:
        logger.warning("SSH probe timed out for %s", host)

    # Step 3: Stale master — attempt automatic recovery.
    logger.warning("Attempting automatic SSH ControlMaster recovery for %s", host)
    try:
        subprocess.run(
            ["ssh", *ssh_options, "-O", "exit", host],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        logger.debug("Could not cleanly exit stale master for %s", host)

    # Step 4: Test a fresh connection (ControlMaster auto will create a new one).
    try:
        fresh = subprocess.run(
            ["ssh", *ssh_options, host, "true"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if fresh.returncode == 0:
            logger.info("SSH ControlMaster for %s recovered automatically", host)
            return
    except subprocess.TimeoutExpired:
        pass

    raise RuntimeError(
        f"SSH ControlMaster for {host} is dead and automatic recovery failed. "
        f"Run 'ssh {host}' manually to re-authenticate (may require 2FA)."
    )


def read_remote_config(snakemake_config: dict) -> dict:
    """Extract and validate remote_solve config section."""
    cfg = snakemake_config["remote_solve"]
    if not cfg["enabled"]:
        raise ValueError(
            "remote_solve.enabled is false; remote solve rules should not be selected."
        )
    user_ssh_options = [str(option) for option in cfg["ssh_options"]]
    # Inject keepalive defaults unless the user already set ServerAliveInterval.
    has_keepalive = any("ServerAliveInterval" in opt for opt in user_ssh_options)
    ssh_options = (
        user_ssh_options if has_keepalive else user_ssh_options + _DEFAULT_SSH_KEEPALIVE
    )
    return {
        "host": str(cfg["host"]),
        "workdir": str(cfg["workdir"]),
        "pixi_env": str(cfg["pixi_env"]),
        "use_slurm": bool(cfg["use_slurm"]),
        "slurm_account": str(cfg["slurm_account"]),
        "slurm_partition": str(cfg["slurm_partition"]),
        "sync_workflow": bool(cfg["sync_workflow"]),
        "sync_pixi_files": bool(cfg["sync_pixi_files"]),
        "preflight_check": bool(cfg["preflight_check"]),
        "ssh_options": ssh_options,
        "rsync_options": [str(option) for option in cfg["rsync_options"]],
    }


def rsync_ssh_args(ssh_options: list[str]) -> list[str]:
    """Build rsync ``-e`` flag to forward SSH options to rsync's transport.

    Returns ``["-e", "ssh -o ... -o ..."]`` or ``[]`` if no options.
    """
    if not ssh_options:
        return []
    return ["-e", "ssh " + " ".join(shlex.quote(o) for o in ssh_options)]


def config_snapshot_rel_path(config_name: str) -> str:
    """Return the project-relative path for the remote config snapshot."""
    return f".snakemake/remote/config_remote_solve_{config_name}.yaml"


def write_config_snapshot(
    snakemake_config: dict, config_name: str, project_root: Path
) -> str:
    """Write a config snapshot with remote_solve disabled. Returns relative path."""
    import copy

    config_snapshot_rel = config_snapshot_rel_path(config_name)
    config_snapshot_path = project_root / config_snapshot_rel
    config_snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    remote_config = copy.deepcopy(dict(snakemake_config))
    remote_config["remote_solve"]["enabled"] = False
    with config_snapshot_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(remote_config, handle, sort_keys=False)

    return config_snapshot_rel


# ---------------------------------------------------------------------------
# Polling daemon management helpers
# ---------------------------------------------------------------------------

# Cache is considered stale after this many seconds (3x default poll interval).
_CACHE_STALENESS_SECONDS = 90


def daemon_paths(jobid_dir: Path) -> dict:
    """Return paths for daemon PID file, cache file, and log file."""
    return {
        "pid_file": jobid_dir / ".poll_daemon.pid",
        "cache_file": jobid_dir / ".job_status_cache.json",
        "log_file": jobid_dir / ".poll_daemon.log",
        "lock_file": jobid_dir / ".poll_daemon.lock",
        "shutdown_marker": jobid_dir / ".poll_daemon_shutdown",
    }


def is_daemon_running(jobid_dir: Path) -> bool:
    """Check if the polling daemon is alive.

    Uses ``os.kill(pid, 0)`` to check process existence, then reads
    ``/proc/{pid}/status`` to exclude zombie processes (which still pass
    the kill check but are not actually running).
    """
    paths = daemon_paths(jobid_dir)
    pid_file = paths["pid_file"]
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)  # Signal 0: check if process exists.
    except (ValueError, OSError):
        return False
    # Check for zombie state — os.kill succeeds for zombies but they're not
    # doing any work.
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("State:"):
                if "Z" in line.split(":")[1]:
                    return False
                break
    except OSError:
        pass
    return True


def signal_daemon_shutdown(jobid_dir: Path, logger) -> bool:
    """Send SIGTERM to the poll daemon. Returns True if signal was sent."""
    paths = daemon_paths(jobid_dir)
    pid_file = paths["pid_file"]
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        logger.info("Sent SIGTERM to poll daemon (PID %d)", pid)
        return True
    except (ValueError, OSError):
        logger.warning("Could not signal poll daemon for shutdown")
        return False


def write_daemon_shutdown_marker(jobid_dir: Path) -> None:
    """Touch a shutdown marker file visible to all daemon instances."""
    paths = daemon_paths(jobid_dir)
    paths["shutdown_marker"].touch()


def start_daemon_if_needed(cfg: dict, jobid_dir: Path, logger) -> None:
    """Start the polling daemon if not already running.

    Uses ``fcntl.flock`` on a lock file to prevent race conditions when
    multiple collect scripts start simultaneously.
    """
    paths = daemon_paths(jobid_dir)
    lock_file = paths["lock_file"]
    jobid_dir.mkdir(parents=True, exist_ok=True)

    with open(lock_file, "w") as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another collect is starting the daemon; it will be ready shortly.
            logger.debug("Daemon lock held by another process; skipping start")
            return

        try:
            if is_daemon_running(jobid_dir):
                logger.debug("Poll daemon already running")
                return

            # Build daemon command.
            daemon_script = Path(__file__).with_name("poll_remote_jobs.py")
            cmd = [
                sys.executable,
                str(daemon_script),
                "--host",
                cfg["host"],
                "--jobid-dir",
                str(jobid_dir),
                "--cache-file",
                str(paths["cache_file"]),
                "--interval",
                "30",
            ]
            # Pass SSH options as a single JSON-encoded list to avoid
            # argparse misinterpreting flag-like values (e.g. "-o") as
            # unknown flags rather than arguments to --ssh-options.
            import json as _json

            cmd.extend(["--ssh-options-json", _json.dumps(cfg["ssh_options"])])

            logger.info("Starting poll daemon: %s", shlex.join(cmd))
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Write PID from parent while still holding the lock, to avoid
            # the race where the child hasn't written it yet.
            paths["pid_file"].write_text(str(proc.pid), encoding="utf-8")
            logger.info("Poll daemon started with PID %d", proc.pid)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def read_job_status_from_cache(
    job_id: str, cache_file: Path
) -> tuple[str, bool, bool] | None:
    """Read a single job's status from the daemon cache.

    Returns ``(state, is_terminal, succeeded)`` or ``None`` if the cache is
    missing, stale (older than 90s), or does not contain the requested job.
    """
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    timestamp = data.get("timestamp", 0)
    if time.time() - timestamp > _CACHE_STALENESS_SECONDS:
        return None

    job_entry = data.get("jobs", {}).get(job_id)
    if job_entry is None:
        return None

    return (
        job_entry["state"],
        job_entry["terminal"],
        job_entry["succeeded"],
    )
