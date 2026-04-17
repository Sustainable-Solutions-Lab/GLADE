# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Background polling daemon that batch-queries SLURM for all active remote jobs.

Instead of each ``collect_remote_solve`` instance opening its own SSH session
every 30 seconds, this daemon runs a single ``squeue`` + ``sacct`` call per
cycle for all jobs and writes results to a local JSON cache file.

Intended to be started automatically by the first ``collect_remote_solve``
instance via :func:`remote_solve_utils.start_daemon_if_needed`.

Lifecycle:
- PID file: ``<jobid_dir>/.poll_daemon.pid``
- Log file: ``<jobid_dir>/.poll_daemon.log``
- Self-terminates after 2 consecutive cycles with no ``.jobid`` files
- Handles SIGTERM and a ``.poll_daemon_shutdown`` marker file for clean shutdown
  (the marker is visible to all daemon instances, not just the PID-file owner)
"""

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import traceback

# Terminal SLURM states (matches collect_remote_solve.py).
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

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


def _discover_job_ids(jobid_dir: Path) -> dict[str, str]:
    """Read all .jobid files and return {job_id: jobid_file_path}.

    Skips files containing "direct" (non-SLURM jobs).
    """
    jobs = {}
    for p in jobid_dir.glob("*.jobid"):
        try:
            job_id = p.read_text(encoding="utf-8").strip()
            if job_id and job_id != "direct":
                jobs[job_id] = str(p)
        except OSError:
            continue
    return jobs


def _run_ssh(host: str, ssh_options: list[str], command: str, logger: logging.Logger):
    """Run an SSH command and return (returncode, stdout)."""
    cmd = ["ssh", *ssh_options, host, command]
    logger.debug("$ %s", shlex.join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("SSH command timed out: %s", command)
        return -1, ""
    except OSError as exc:
        logger.warning("SSH command failed: %s", exc)
        return -1, ""


def _batch_query(
    host: str,
    ssh_options: list[str],
    job_ids: list[str],
    logger: logging.Logger,
) -> dict[str, dict]:
    """Query SLURM for all job_ids in a single SSH call. Returns status dict."""
    if not job_ids:
        return {}

    results = {}
    id_list = ",".join(job_ids)

    # squeue for running jobs.
    squeue_cmd = f"squeue -j {shlex.quote(id_list)} -h -o '%i %T'"
    rc, stdout = _run_ssh(host, ssh_options, squeue_cmd, logger)

    seen_in_squeue = set()
    if rc == 0:
        for line in stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                jid, state = parts[0], parts[1]
                seen_in_squeue.add(jid)
                results[jid] = {
                    "state": state,
                    "terminal": False,
                    "succeeded": False,
                }

    # sacct for jobs not in squeue (completed/failed).
    missing = [jid for jid in job_ids if jid not in seen_in_squeue]
    if missing:
        missing_list = ",".join(missing)
        sacct_cmd = (
            f"sacct -j {shlex.quote(missing_list)}"
            " --format=JobID,State --noheader --parsable2"
        )
        rc, stdout = _run_ssh(host, ssh_options, sacct_cmd, logger)
        if rc == 0:
            for line in stdout.strip().splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 2:
                    jid, state = parts[0].strip(), parts[1].strip()
                    # sacct can return sub-job lines like "12345.batch"; skip those.
                    if "." in jid:
                        continue
                    if jid in missing:
                        if state == "COMPLETED":
                            results[jid] = {
                                "state": state,
                                "terminal": True,
                                "succeeded": True,
                            }
                        elif state.split()[0] in _SLURM_FAILED_STATES:
                            results[jid] = {
                                "state": state,
                                "terminal": True,
                                "succeeded": False,
                            }
                        else:
                            results[jid] = {
                                "state": state,
                                "terminal": False,
                                "succeeded": False,
                            }

    return results


def _write_cache(cache_file: Path, jobs: dict[str, dict], logger: logging.Logger):
    """Atomically write the job status cache."""
    cache_data = {
        "timestamp": time.time(),
        "jobs": jobs,
    }
    tmp = cache_file.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cache_data), encoding="utf-8")
        os.replace(str(tmp), str(cache_file))
    except OSError as exc:
        logger.warning("Failed to write cache: %s", exc)


def _cancel_all_jobs(
    host: str,
    ssh_options: list[str],
    job_ids: list[str],
    logger: logging.Logger,
):
    """Cancel all tracked SLURM jobs in a single scancel call."""
    if not job_ids:
        return
    id_list = ",".join(job_ids)
    cmd = ["ssh", *ssh_options, host, f"scancel {id_list}"]
    logger.info("Cancelling %d remote jobs: %s", len(job_ids), id_list)
    try:
        subprocess.run(cmd, timeout=30, check=False, capture_output=True, text=True)
        logger.info("Cancel command sent")
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Could not cancel jobs: %s", exc)


def _main_inner(args, jobid_dir: Path, cache_file: Path, logger: logging.Logger):
    """Core poll loop. Exceptions propagate to the crash-safe wrapper."""
    global _shutdown_requested

    # PID file is written by the parent process (start_daemon_if_needed);
    # we only clean it up on exit.
    pid_file = jobid_dir / ".poll_daemon.pid"

    # Register signal handlers for clean shutdown.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "Poll daemon started (pid=%d, host=%s, interval=%ds)",
        os.getpid(),
        args.host,
        args.interval,
    )

    # Verify SSH connectivity before entering the poll loop. If the
    # ControlMaster is stale (e.g. after a network interruption), fail
    # fast so the user can re-authenticate. Collect instances will
    # restart the daemon on the next poll cycle.
    rc, _ = _run_ssh(args.host, args.ssh_options, "true", logger)
    if rc != 0:
        raise RuntimeError(
            f"SSH connection to {args.host} failed (rc={rc}). "
            f"Run 'ssh {args.host}' manually to re-authenticate."
        )

    empty_cycles = 0
    # Jobs already known to be in a terminal state; no need to re-query.
    terminal_cache: dict[str, dict] = {}
    # Most recent job map from _discover_job_ids; used as fallback in finally.
    job_map: dict[str, str] = {}
    shutdown_marker = jobid_dir / ".poll_daemon_shutdown"

    try:
        while not _shutdown_requested:
            # Check for file-based shutdown marker (visible to all instances).
            if shutdown_marker.exists():
                logger.info("Shutdown marker found")
                _shutdown_requested = True
                break

            job_map = _discover_job_ids(jobid_dir)

            if not job_map:
                empty_cycles += 1
                logger.info("No .jobid files found (empty cycle %d/2)", empty_cycles)
                if empty_cycles >= 2:
                    logger.info("No jobs for 2 consecutive cycles; exiting")
                    break
                # Still write an empty cache so readers see a fresh timestamp.
                _write_cache(cache_file, {}, logger)
                time.sleep(args.interval)
                continue

            empty_cycles = 0

            # Only query jobs not already known to be terminal.
            pending_ids = [jid for jid in job_map if jid not in terminal_cache]
            if pending_ids:
                logger.info(
                    "Polling %d jobs: %s", len(pending_ids), ", ".join(pending_ids)
                )
                fresh = _batch_query(args.host, args.ssh_options, pending_ids, logger)
                # Move newly terminal jobs into the local cache.
                for jid, status in fresh.items():
                    if status["terminal"]:
                        terminal_cache[jid] = status
            else:
                fresh = {}
                logger.info(
                    "All %d jobs already terminal; waiting for .jobid cleanup",
                    len(job_map),
                )

            # Merge: terminal_cache (stable) + fresh non-terminal results.
            merged = {**terminal_cache, **fresh}
            # Prune terminal_cache entries whose .jobid files are gone.
            terminal_cache = {
                jid: s for jid, s in terminal_cache.items() if jid in job_map
            }
            _write_cache(cache_file, merged, logger)

            logger.info(
                "Cache updated: %d statuses written",
                len(merged),
            )

            # Sleep in short increments so we can respond to signals and
            # the shutdown marker promptly.
            for _ in range(args.interval):
                if _shutdown_requested or shutdown_marker.exists():
                    _shutdown_requested = True
                    break
                time.sleep(1)
    finally:
        if _shutdown_requested:
            # Re-discover from .jobid files (most reliable source of current
            # job IDs); fall back to the last poll's job_map.
            current_jobs = _discover_job_ids(jobid_dir)
            jobs_to_cancel = list(current_jobs.keys()) or list(job_map.keys())
            if jobs_to_cancel:
                _cancel_all_jobs(args.host, args.ssh_options, jobs_to_cancel, logger)
        shutdown_marker.unlink(missing_ok=True)
        # Only clean up PID file if it still contains our PID; a newer
        # daemon may have been started and written its PID already.
        try:
            if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        logger.info("Poll daemon exiting")


def main():
    parser = argparse.ArgumentParser(
        description="Poll SLURM jobs and write status cache"
    )
    parser.add_argument("--host", required=True, help="SSH host")
    parser.add_argument(
        "--ssh-options-json",
        default="[]",
        help="SSH options as a JSON-encoded list",
    )
    parser.add_argument(
        "--jobid-dir", required=True, help="Directory containing .jobid files"
    )
    parser.add_argument("--cache-file", required=True, help="Output JSON cache path")
    parser.add_argument(
        "--interval", type=int, default=30, help="Poll interval in seconds"
    )
    args = parser.parse_args()
    args.ssh_options = json.loads(args.ssh_options_json)

    # Resolve all paths to absolute immediately, so the daemon is robust
    # even if the working directory changes unexpectedly.
    jobid_dir = Path(args.jobid_dir).resolve()
    cache_file = Path(args.cache_file).resolve()
    log_file = jobid_dir / ".poll_daemon.log"

    # Redirect stderr to an error log as early as possible, so any crash
    # output (import errors, segfaults, etc.) is captured even if the
    # crash-safe wrapper below fails.
    err_file = jobid_dir / ".poll_daemon.err"
    with contextlib.suppress(OSError):
        sys.stderr = open(err_file, "a")  # noqa: SIM115

    # Crash-safe wrapper: capture any exception (including SystemExit,
    # KeyboardInterrupt, and errors before logging is configured) to
    # the log file via plain open().
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.FileHandler(str(log_file))],
        )
        logger = logging.getLogger("poll_daemon")
        _main_inner(args, jobid_dir, cache_file, logger)
    except BaseException:
        with contextlib.suppress(OSError), open(log_file, "a") as f:
            f.write(f"\n--- DAEMON CRASH ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
            traceback.print_exc(file=f)
        sys.exit(1)


if __name__ == "__main__":
    main()
