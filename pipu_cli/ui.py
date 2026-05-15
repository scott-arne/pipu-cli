"""Upgrade UI display layer using Rich progress components."""

import threading
import time
from dataclasses import dataclass
from pathlib import PurePath, PureWindowsPath
from types import TracebackType
from typing import Callable, Dict, List, Optional, Type

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TaskID
from rich.text import Text


CHECKMARK = "\u2713"
CROSS = "\u2717"
BULLET = "\u25cc"
DOT = "\u00b7"

STYLES = {
    "success": "green",
    "failure": "red",
    "warning": "yellow",
    "info": "cyan",
    "dim": "dim",
}

ENV_NAME_MAX = 16
PKG_NAME_MAX = 20
INSTALL_STATUS_MAX = 48


def _fit(name: str, width: int) -> str:
    """Truncate or pad a string to exactly *width* visible characters."""
    if len(name) <= width:
        return name.ljust(width)
    return name[: width - 1] + "\u2026"


@dataclass
class _DownloadState:
    """Live progress state for one active package download."""

    downloaded: Optional[int]
    total: Optional[int]
    last_activity: float
    has_activity: bool = False
    status: str = "receiving data"


class _DownloadStatusRenderable:
    """Renderable wrapper so idle ages update between download events."""

    def __init__(self, tracker: "DownloadTracker") -> None:
        self._tracker = tracker

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console, options
        lines = self._tracker._render_active_lines()
        if lines.plain:
            yield lines


def _format_bytes(size: int) -> str:
    """Format a byte count compactly for one-line progress rows."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _path_name(value: str) -> str:
    """Return the filename portion from a pip-reported local path."""
    cleaned = value.strip().strip("'\"")
    if "\\" in cleaned and "/" not in cleaned:
        return PureWindowsPath(cleaned).name
    return PurePath(cleaned).name


def _summarize_install_activity(message: str) -> str:
    """Compact noisy pip install lines for one-line environment status."""
    status = message.strip()
    if status.startswith("Processing "):
        target = status.removeprefix("Processing ").strip()
        name = _path_name(target)
        if name and name != target:
            return f"Processing {name}"
    return status


class DownloadTracker:
    """Single progress bar with a compact bulleted list of active downloads below.

    Thread-safe for use with parallel downloads.
    """

    def __init__(
        self,
        progress: Progress,
        task_id: TaskID,
        total: int,
        *,
        idle_timeout: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._live: Optional[Live] = None
        self._progress = progress
        self._task_id = task_id
        self._total = total
        self._completed = 0
        self._failed = 0
        self._active: Dict[str, _DownloadState] = {}
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._lock = threading.Lock()

    def attach_live(self, live: Live) -> None:
        """Attach the Rich live display after renderable construction."""
        self._live = live

    def start(self, spec: str) -> None:
        """Mark a package as actively downloading.

        :param spec: Package spec (e.g., "requests==2.31.0")
        """
        with self._lock:
            now = self._clock()
            self._active[spec] = _DownloadState(
                downloaded=None,
                total=None,
                last_activity=now,
            )
        self._refresh()

    def activity(self, spec: str, status: str = "receiving data") -> None:
        """Record download activity without displaying artifact-level progress.

        :param spec: Package spec.
        :param status: Compact status message for the current activity.
        """
        with self._lock:
            now = self._clock()
            state = self._active.get(spec)
            if state is None:
                state = _DownloadState(
                    downloaded=None,
                    total=None,
                    last_activity=now,
                    has_activity=True,
                    status=status,
                )
                self._active[spec] = state
            else:
                state.last_activity = now
                state.has_activity = True
                state.status = status
        self._refresh()

    def progress(self, spec: str, downloaded: int, total: Optional[int]) -> None:
        """Record byte-level progress for an active package.

        :param spec: Package spec.
        :param downloaded: Bytes downloaded so far.
        :param total: Total bytes expected, or ``None`` when pip does not know.
        """
        with self._lock:
            state = self._active.get(spec)
            if state is None:
                state = _DownloadState(downloaded=None, total=None, last_activity=self._clock())
                self._active[spec] = state
            state.downloaded = downloaded
            state.total = total
            state.has_activity = True
            state.last_activity = self._clock()
        self._refresh()

    def complete(self, spec: str) -> None:
        """Mark a package download as complete.

        :param spec: Package spec
        """
        with self._lock:
            self._active.pop(spec, None)
            self._completed += 1
            self._progress.update(self._task_id, completed=self._completed + self._failed)
        self._refresh()

    def fail(self, spec: str) -> None:
        """Mark a package download as failed.

        :param spec: Package spec
        """
        with self._lock:
            self._active.pop(spec, None)
            self._failed += 1
            self._progress.update(self._task_id, completed=self._completed + self._failed)
        self._refresh()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.refresh()

    def _render_active_lines(self) -> Text:
        """Render active package rows below the aggregate progress bar."""
        now = self._clock()
        with self._lock:
            snapshot = sorted(
                (
                    spec,
                    state.downloaded,
                    state.total,
                    state.last_activity,
                    state.has_activity,
                    state.status,
                )
                for spec, state in self._active.items()
            )

        lines = Text()
        for index, (
            spec,
            downloaded,
            total,
            last_activity,
            has_activity,
            status,
        ) in enumerate(snapshot):
            if index:
                lines.append("\n")
            lines.append(f"    {BULLET} ", style="dim")
            lines.append(spec, style="dim")
            detail = self._format_progress(downloaded, total, has_activity, status)
            if detail:
                lines.append("  ", style="dim")
                age = max(0.0, now - last_activity)
                lines.append(detail, style=self._progress_style(downloaded, total, age))
        return lines

    def _format_progress(
        self,
        downloaded: Optional[int],
        total: Optional[int],
        has_activity: bool,
        status: str,
    ) -> str:
        if downloaded is None:
            if has_activity:
                return status
            return ""
        if total is None:
            return _format_bytes(downloaded)
        percent = min(100.0, (downloaded / total) * 100) if total else 0.0
        return f"{_format_bytes(downloaded)} / {_format_bytes(total)} {percent:.0f}%"

    def _activity_thresholds(self) -> tuple[float, float]:
        if self._idle_timeout is None:
            return 5.0, 10.0
        return self._idle_timeout * 0.5, self._idle_timeout * 0.9

    def _progress_style(
        self,
        downloaded: Optional[int],
        total: Optional[int],
        age: float,
    ) -> str:
        if downloaded is None:
            warning_age, critical_age = self._activity_thresholds()
            if age >= critical_age:
                return "red"
            if age >= warning_age:
                return "yellow"
            return "green"
        if total is None:
            return "dim"
        if total is not None and downloaded is not None and downloaded >= total:
            return "green"
        warning_age, critical_age = self._activity_thresholds()
        if age >= critical_age:
            return "red"
        if age >= warning_age:
            return "yellow"
        return "green"

    def finish(self) -> None:
        """Stop the progress display."""
        if self._live is not None:
            self._live.update(self._progress)
            self._live.stop()

    def cleanup(self) -> None:
        """Stop the progress display. Safe to call multiple times.

        Idempotent alias for :meth:`finish` that swallows errors from an
        already-stopped :class:`~rich.live.Live`.
        """
        try:
            self.finish()
        except Exception:
            pass

    def __enter__(self) -> "DownloadTracker":
        """Enter the context manager; the live display is already running."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Stop the live display and unconditionally restore the cursor.

        :param exc_type: Exception type (unused).
        :param exc: Exception instance (unused).
        :param tb: Traceback (unused).
        :returns: ``None`` so exceptions propagate.
        """
        try:
            self.cleanup()
        finally:
            try:
                if self._live is not None:
                    self._live.console.show_cursor(True)
            except Exception:
                # Swallow errors during interrupt cleanup: stdout may already be closed.
                pass


class GroupInstallTracker:
    """Tracks per-environment install progress with fixed-width aligned bars.

    Thread-safe for use with parallel per-environment installs.
    """

    def __init__(
        self,
        progress: Progress,
        tasks: Dict[str, TaskID],
        totals: Dict[str, int],
        env_width: int,
        count_width: int,
    ) -> None:
        self._progress = progress
        self._tasks = tasks
        self._totals = totals
        self._completed: Dict[str, int] = {name: 0 for name in tasks}
        self._env_width = env_width
        self._count_width = count_width
        self._lock = threading.Lock()

    def _make_desc(self, icon: str, env_name: str) -> str:
        return f"  {icon} [cyan]{_fit(env_name, self._env_width)}[/cyan]"

    def _make_count(self, count: int, total: int) -> str:
        return f"{count}/{total}".rjust(self._count_width)

    def start_env(self, env_name: str) -> None:
        """Mark an environment as actively installing.

        :param env_name: Short environment name.
        """
        with self._lock:
            if env_name in self._tasks:
                total = self._totals[env_name]
                count = self._completed.get(env_name, 0)
                self._progress.update(
                    self._tasks[env_name],
                    completed=count,
                    description=self._make_desc(DOT, env_name),
                    count=self._make_count(count, total),
                    pkg="installing...",
                )

    def message_env(self, env_name: str, message: str) -> None:
        """Show the latest install activity for an environment.

        :param env_name: Short environment name.
        :param message: Latest pip output line.
        """
        status = _summarize_install_activity(message)
        if not status:
            return
        with self._lock:
            if env_name in self._tasks:
                total = self._totals[env_name]
                count = self._completed.get(env_name, 0)
                self._progress.update(
                    self._tasks[env_name],
                    completed=count,
                    description=self._make_desc(DOT, env_name),
                    count=self._make_count(count, total),
                    pkg=_fit(status, INSTALL_STATUS_MAX),
                )

    def advance(self, env_name: str, package_name: str) -> None:
        """Record a package install completion for an environment.

        :param env_name: Short environment name
        :param package_name: Package that was just installed
        """
        with self._lock:
            if env_name in self._tasks:
                self._completed[env_name] += 1
                count = self._completed[env_name]
                total = self._totals[env_name]
                self._progress.update(
                    self._tasks[env_name],
                    completed=count,
                    description=self._make_desc(DOT, env_name),
                    count=self._make_count(count, total),
                    pkg=_fit(package_name, PKG_NAME_MAX),
                )

    def complete_env(self, env_name: str) -> None:
        """Mark an environment as fully complete.

        :param env_name: Short environment name
        """
        with self._lock:
            if env_name in self._tasks:
                total = self._totals[env_name]
                self._progress.update(
                    self._tasks[env_name],
                    completed=total,
                    description=self._make_desc(f"[bold green]{CHECKMARK}[/bold green]", env_name),
                    count=self._make_count(total, total),
                    pkg="",
                )

    def fail_env(self, env_name: str, reason: str) -> None:
        """Mark an environment as failed.

        :param env_name: Short environment name
        :param reason: Failure reason
        """
        with self._lock:
            if env_name in self._tasks:
                total = self._totals[env_name]
                count = self._completed.get(env_name, 0)
                self._progress.update(
                    self._tasks[env_name],
                    completed=count,
                    description=self._make_desc(f"[bold red]{CROSS}[/bold red]", env_name),
                    count=self._make_count(count, total),
                    pkg=f"[red]{reason}[/red]",
                )

    def finish(self) -> None:
        """Stop the progress display."""
        self._progress.stop()

    def cleanup(self) -> None:
        """Stop the progress display. Safe to call multiple times.

        Idempotent alias for :meth:`finish` that swallows errors from an
        already-stopped :class:`~rich.progress.Progress`.
        """
        try:
            self.finish()
        except Exception:
            pass

    def __enter__(self) -> "GroupInstallTracker":
        """Enter the context manager; the progress display is already running."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Stop the progress display and unconditionally restore the cursor.

        :param exc_type: Exception type (unused).
        :param exc: Exception instance (unused).
        :param tb: Traceback (unused).
        :returns: ``None`` so exceptions propagate.
        """
        try:
            self.cleanup()
        finally:
            try:
                self._progress.console.show_cursor(True)
            except Exception:
                # Swallow errors during interrupt cleanup: stdout may already be closed.
                pass


class UpgradeUI:
    """Manages upgrade command display: spinner/checkmark phases and progress trackers."""

    def __init__(self, console: Console) -> None:
        """Initialize with a Rich console.

        :param console: Rich Console for output
        """
        self.console = console
        self._active_phase: Optional[Progress] = None
        self._active_task_id: Optional[int] = None
        self._active_description: Optional[str] = None

    def cleanup(self) -> None:
        """Stop any active progress and restore terminal state."""
        if self._active_phase is not None:
            try:
                self._active_phase.stop()
            except Exception:
                pass
            self._active_phase = None
            self._active_task_id = None
            self._active_description = None
        self.console.show_cursor(True)

    def __enter__(self) -> "UpgradeUI":
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Stop any active progress and unconditionally restore the cursor.

        :param exc_type: Exception type (unused).
        :param exc: Exception instance (unused).
        :param tb: Traceback (unused).
        :returns: ``None`` so exceptions propagate.
        """
        try:
            self.cleanup()
        finally:
            try:
                self.console.show_cursor(True)
            except Exception:
                # Swallow errors during interrupt cleanup: stdout may already be closed.
                pass

    def start_phase(self, description: str) -> None:
        """Start a new phase with a spinner.

        :param description: Phase description (e.g., "Inspecting installed packages...")
        """
        if self._active_phase is not None:
            self._active_phase.stop()
            self._active_phase = None

        self._active_description = description
        self._active_phase = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=True,
        )
        self._active_phase.start()
        self._active_task_id = self._active_phase.add_task(description, total=None)

    def complete_phase(self, summary: str) -> None:
        """Complete the active phase: stop spinner, print checkmark with summary.

        :param summary: Summary text (e.g., "Found 182 packages")
        :raises RuntimeError: If no phase is active
        """
        if self._active_phase is None:
            raise RuntimeError("No active phase to complete")

        description = self._active_description
        self._active_phase.stop()
        self._active_phase = None
        self._active_task_id = None
        self._active_description = None

        self.console.print(f"[bold green]{CHECKMARK}[/bold green] {description} [dim]{summary}[/dim]")

    def show_download_progress(
        self,
        specs: List[str],
        label: str = "Downloading",
        *,
        idle_timeout: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> DownloadTracker:
        """Show a single progress bar for downloading with active-package list.

        :param specs: List of package specs to download
        :param label: Label for the progress bar
        :param idle_timeout: Optional idle timeout used to color idle rows.
        :param clock: Monotonic clock for live idle-age calculation.
        :returns: DownloadTracker for updating progress
        """
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
        )
        task_id = progress.add_task(f"  {label}", total=len(specs))
        tracker = DownloadTracker(
            progress,
            task_id,
            len(specs),
            idle_timeout=idle_timeout,
            clock=clock,
        )
        live = Live(
            Group(progress, _DownloadStatusRenderable(tracker)),
            console=self.console,
            refresh_per_second=8,
        )
        tracker.attach_live(live)
        live.start()
        return tracker

    def show_install_progress(self, specs: List[str], label: str = "Installing") -> DownloadTracker:
        """Show a single progress bar for installing with active-package list.

        :param specs: List of package specs to install
        :param label: Label for the progress bar
        :returns: DownloadTracker for updating progress
        """
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
        )
        task_id = progress.add_task(f"  {label}", total=len(specs))
        tracker = DownloadTracker(progress, task_id, len(specs))
        live = Live(
            Group(progress, _DownloadStatusRenderable(tracker)),
            console=self.console,
            refresh_per_second=8,
        )
        tracker.attach_live(live)
        live.start()
        return tracker

    def show_group_install_progress(
        self, env_names: List[str], env_totals: Dict[str, int]
    ) -> GroupInstallTracker:
        """Show per-environment install progress bars with stable alignment.

        :param env_names: Ordered list of short environment names
        :param env_totals: Dict mapping env name to total package count
        :returns: GroupInstallTracker for updating progress
        """
        env_width = min(max((len(n) for n in env_names), default=4), ENV_NAME_MAX)
        max_total = max(env_totals.values(), default=0)
        count_width = len(f"{max_total}/{max_total}")

        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.fields[count]}"),
            TextColumn("{task.fields[pkg]}", style="dim"),
            console=self.console,
            transient=False,
        )
        progress.start()
        tasks = {}
        for name in env_names:
            total = env_totals.get(name, 0)
            desc = f"  {DOT} [cyan]{_fit(name, env_width)}[/cyan]"
            count_str = f"0/{total}".rjust(count_width)
            task_id = progress.add_task(desc, total=total, count=count_str, pkg="")
            tasks[name] = task_id
        return GroupInstallTracker(progress, tasks, env_totals, env_width, count_width)
