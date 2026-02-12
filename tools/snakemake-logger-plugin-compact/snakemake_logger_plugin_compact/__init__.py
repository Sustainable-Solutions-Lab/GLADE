# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact Snakemake logger plugin with Rich live status display.

Shows one line per rule start/finish, with a continuously updating live
display at the bottom of the terminal showing overall progress and each
currently running job with its elapsed time.
"""

import re
import time

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from snakemake_interface_logger_plugins.base import LogHandlerBase
from snakemake_interface_logger_plugins.common import LogEvent


def _format_wildcards(wildcards):
    """Format wildcards dict as compact key=val pairs."""
    if not wildcards:
        return ""
    parts = [f"{k}={v}" for k, v in wildcards.items()]
    return ", ".join(parts)


class LogHandler(LogHandlerBase):
    """Compact log handler with Rich live status display."""

    @property
    def writes_to_stream(self):
        return True

    @property
    def writes_to_file(self):
        return False

    @property
    def has_filter(self):
        return True

    @property
    def has_formatter(self):
        return True

    @property
    def needs_rulegraph(self):
        return False

    def __post_init__(self):
        nocolor = getattr(self.common_settings, "nocolor", False)
        self._show_failed_logs = getattr(
            self.common_settings, "show_failed_logs", False
        )
        self._dryrun = getattr(self.common_settings, "dryrun", False)

        self._console = Console(stderr=True, no_color=nocolor)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
        )
        self._rule = Rule()
        self._live = None

        # State tracking
        self._running_jobs = {}  # jobid -> Rich task_id
        self._job_info = {}  # jobid -> (rule_name, wc_str)
        self._done = 0
        self._total = 0

    def _ensure_live(self):
        """Start the Live display on first real job (TTY only, non-dryrun)."""
        if self._live or self._dryrun or not self._console.is_terminal:
            return
        if self._total > 0:
            self._rule = Rule(title=f"0/{self._total} (0%)")
        else:
            self._rule = Rule(title="0/?")
        self._live = Live(
            Group(self._rule, self._progress),
            console=self._console,
            transient=True,
            refresh_per_second=1,
        )
        self._live.start()

    def _log(self, text, style=None):
        """Print a log line above the live area (or directly if no live)."""
        kwargs = {"style": style, "highlight": False, "markup": False}
        if self._live and self._live.is_started:
            self._live.console.print(text, **kwargs)
        else:
            self._console.print(text, **kwargs)

    def _update_rule(self):
        """Rebuild the Rule title with current progress."""
        if self._total > 0:
            pct = self._done * 100 // self._total
            self._rule = Rule(title=f"{self._done}/{self._total} ({pct}%)")
        else:
            self._rule = Rule()
        if self._live and self._live.is_started:
            self._live.update(Group(self._rule, self._progress))

    def _stop_live(self):
        """Stop the live display if running."""
        if self._live and self._live.is_started:
            self._live.stop()

    def filter(self, record):
        event = record.__dict__.get("event", None)
        return event not in (
            LogEvent.SHELLCMD,
            LogEvent.RESOURCES_INFO,
            LogEvent.DEBUG_DAG,
        )

    def emit(self, record):
        try:
            event = record.__dict__.get("event", None)
            rd = record.__dict__

            if event == LogEvent.JOB_INFO:
                self._emit_job_info(rd)

            elif event == LogEvent.JOB_FINISHED:
                self._emit_job_finished(rd)

            elif event == LogEvent.JOB_ERROR:
                self._emit_job_error(rd)

            elif event == LogEvent.GROUP_INFO:
                msg = rd.get("msg", "")
                self._log(f"[{time.strftime('%H:%M:%S')}] {msg}", style="green")

            elif event == LogEvent.GROUP_ERROR:
                self._emit_group_error(rd)

            elif event == LogEvent.PROGRESS:
                self._emit_progress(rd)

            elif event == LogEvent.RUN_INFO:
                msg = rd.get("msg", "")
                if msg:
                    self._log(str(msg))

            elif event == LogEvent.ERROR:
                msg = rd.get("msg", "")
                if msg:
                    self._log(str(msg), style="red")

            elif event is None:
                msg = self.format(record) if hasattr(record, "msg") else ""
                if msg and msg != "None":
                    # Filter Snakemake job-selection noise
                    if msg.startswith("Select jobs to execute") or (
                        msg.startswith("Execute ") and " jobs" in msg
                    ):
                        return
                    # Extract total from job stats table
                    m = re.match(r"^total\s+(\d+)$", msg)
                    if m:
                        self._total = int(m.group(1))
                        self._update_rule()
                    self._log(str(msg))

        except (BrokenPipeError, KeyboardInterrupt, SystemExit):
            pass
        except Exception:
            self.handleError(record)

    def format(self, record):
        """Format generic log records (no event)."""
        return record.getMessage()

    def _emit_job_info(self, rd):
        """Handle JOB_INFO: log line, add to progress, start live display."""
        rule_name = rd.get("rule_name", "?")
        wildcards = rd.get("wildcards", {})
        jobid = rd.get("jobid")
        wc_str = _format_wildcards(wildcards)

        ts = time.strftime("%H:%M:%S")
        wc = f" ({wc_str})" if wc_str else ""
        self._log(f"[{ts}] → {rule_name}{wc}", style="green")

        # Track job and add to progress display
        if jobid is not None and not self._dryrun:
            desc = f" {rule_name} ({wc_str})" if wc_str else f" {rule_name}"
            self._job_info[jobid] = (rule_name, wc_str)
            task_id = self._progress.add_task(desc, total=None)
            self._running_jobs[jobid] = task_id
            self._ensure_live()

    def _emit_job_finished(self, rd):
        """Handle JOB_FINISHED: log done line, remove from progress."""
        # JOB_FINISHED uses 'job_id' (not 'jobid')
        jobid = rd.get("job_id")

        rule_name, wc_str = self._job_info.pop(jobid, ("?", ""))

        ts = time.strftime("%H:%M:%S")
        wc = f" ({wc_str})" if wc_str else ""
        self._log(f"[{ts}] ✓ {rule_name}{wc}", style="green")

        task_id = self._running_jobs.pop(jobid, None)
        if task_id is not None:
            self._progress.remove_task(task_id)

    def _emit_progress(self, rd):
        """Handle PROGRESS: update counters and rule display."""
        self._done = rd.get("done", 0)
        self._total = rd.get("total", 0)
        self._update_rule()

        # Non-TTY: print standalone progress line
        if not self._console.is_terminal and self._total > 0:
            pct = self._done * 100 // self._total
            ts = time.strftime("%H:%M:%S")
            self._log(f"[{ts}] {self._done} of {self._total} steps ({pct}%) done")

        # Stop live display when workflow is complete
        if self._done == self._total and not self._running_jobs:
            self._stop_live()

    def _emit_job_error(self, rd):
        """Print full error details for a failed job."""
        jobid = rd.get("jobid")
        rule_name = rd.get("rule_name", "?")
        rule_msg = rd.get("rule_msg", "")
        log_files = rd.get("log", [])

        # Remove from progress display
        if jobid is not None:
            self._job_info.pop(jobid, None)
            task_id = self._running_jobs.pop(jobid, None)
            if task_id is not None:
                self._progress.remove_task(task_id)

        ts = time.strftime("%H:%M:%S")
        lines = [f"[{ts}] Error in rule {rule_name} (jobid {jobid}):"]
        if rule_msg:
            lines.append(f"    message: {rule_msg}")
        if log_files:
            lines.append(f"    log: {', '.join(log_files)}")

        self._log("\n".join(lines), style="red")

        if self._show_failed_logs and log_files:
            self._show_log_files(log_files)

    def _emit_group_error(self, rd):
        """Print full error details for a failed group."""
        msg = rd.get("msg", "")
        ts = time.strftime("%H:%M:%S")
        lines = [f"[{ts}] Group error"]
        if msg:
            lines.append(f"    message: {msg}")

        job_error_info = rd.get("job_error_info", [])
        for info in job_error_info:
            name = info.get("name", "?")
            jobid = info.get("jobid", "?")
            log_files = info.get("log", [])
            lines.append(f"    rule {name} (jobid {jobid})")
            if log_files:
                lines.append(f"        log: {', '.join(log_files)}")

            # Clean up progress tracking
            if isinstance(jobid, int):
                self._job_info.pop(jobid, None)
                task_id = self._running_jobs.pop(jobid, None)
                if task_id is not None:
                    self._progress.remove_task(task_id)

        self._log("\n".join(lines), style="red")

        all_logs = rd.get("aux_logs", [])
        for info in job_error_info:
            all_logs.extend(info.get("log", []))
        if self._show_failed_logs and all_logs:
            self._show_log_files(all_logs)

    def _show_log_files(self, log_files):
        """Display contents of log files for failed jobs."""
        for f in log_files:
            try:
                with open(f) as fh:
                    content = fh.read()
            except (FileNotFoundError, UnicodeDecodeError) as e:
                self._log(f"    (could not read log {f}: {e})")
                continue
            if not content.strip():
                self._log(f"    Logfile {f}: empty")
                continue
            self._log(f"    Logfile {f}:")
            header_len = min(max(len(s) for s in content.splitlines()), 80)
            self._log("    " + "=" * header_len)
            for line in content.splitlines():
                self._log("    " + line)
            self._log("    " + "=" * header_len)
