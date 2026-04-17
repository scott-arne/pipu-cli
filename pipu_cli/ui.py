"""Upgrade UI display layer using Rich progress components."""

import threading
import time
from typing import Dict, List, Optional, Set

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TaskID
from rich.text import Text


CHECKMARK = "[bold green]\u2713[/bold green]"
CROSS = "[bold red]\u2717[/bold red]"

PKG_NAME_WIDTH = 30


def _fit_name(name: str, width: int = PKG_NAME_WIDTH) -> str:
    """Truncate or pad a package name to exactly *width* characters."""
    if len(name) <= width:
        return name.ljust(width)
    return name[: width - 1] + "\u2026"


class DownloadTracker:
    """Single progress bar with currently-active package names below it.

    Thread-safe for use with parallel downloads.  Long package names scroll
    horizontally (marquee style) in the active-downloads line.
    """

    MARQUEE_WIDTH = 28
    MARQUEE_SPEED = 6

    def __init__(self, live: Live, progress: Progress, task_id: TaskID, total: int) -> None:
        self._live = live
        self._progress = progress
        self._task_id = task_id
        self._total = total
        self._completed = 0
        self._failed = 0
        self._active: Set[str] = set()
        self._start_times: Dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self, spec: str) -> None:
        """Mark a package as actively downloading.

        :param spec: Package spec (e.g., "requests==2.31.0")
        """
        with self._lock:
            self._active.add(spec)
            self._start_times[spec] = time.monotonic()
            self._refresh()

    def complete(self, spec: str) -> None:
        """Mark a package download as complete.

        :param spec: Package spec
        """
        with self._lock:
            self._active.discard(spec)
            self._start_times.pop(spec, None)
            self._completed += 1
            self._progress.update(self._task_id, completed=self._completed + self._failed)
            self._refresh()

    def fail(self, spec: str, _reason: str = "") -> None:
        """Mark a package download as failed.

        :param spec: Package spec
        :param _reason: Failure reason (unused, logged at download layer)
        """
        with self._lock:
            self._active.discard(spec)
            self._start_times.pop(spec, None)
            self._failed += 1
            self._progress.update(self._task_id, completed=self._completed + self._failed)
            self._refresh()

    def _marquee(self, name: str) -> str:
        w = self.MARQUEE_WIDTH
        if len(name) <= w:
            return name.ljust(w)
        padded = name + "   "
        elapsed = time.monotonic() - self._start_times.get(name, time.monotonic())
        offset = int(elapsed * self.MARQUEE_SPEED) % len(padded)
        rotated = padded[offset:] + padded[:offset]
        return rotated[:w]

    def _refresh(self) -> None:
        if self._active:
            parts: list = []
            for i, spec in enumerate(sorted(self._active)):
                if i > 0:
                    parts.append((", ", "dim"))
                parts.append((self._marquee(spec), "dim"))
            active_text = Text.assemble(*parts)
            self._live.update(Group(self._progress, active_text))
        else:
            self._live.update(Group(self._progress))

    def finish(self) -> None:
        """Stop the progress display."""
        self._live.update(self._progress)
        self._live.stop()


class GroupInstallTracker:
    """Tracks per-environment install progress with parallel bars."""

    def __init__(self, progress: Progress, tasks: Dict[str, TaskID], totals: Dict[str, int]) -> None:
        self._progress = progress
        self._tasks = tasks
        self._totals = totals
        self._completed: Dict[str, int] = {name: 0 for name in tasks}
        self._max_env_len = max((len(n) for n in tasks), default=4)

    def _fmt_desc(self, prefix: str, env_name: str, count: int, total: int, suffix: str = "") -> str:
        env_pad = env_name.ljust(self._max_env_len)
        count_str = f"{count}/{total}"
        base = f"  {prefix}[cyan]{env_pad}[/cyan]  {count_str}"
        if suffix:
            return f"{base}  [dim]{_fit_name(suffix)}[/dim]"
        return base

    def advance(self, env_name: str, package_name: str) -> None:
        """Record a package install completion for an environment.

        :param env_name: Short environment name
        :param package_name: Package that was just installed
        """
        if env_name in self._tasks:
            self._completed[env_name] += 1
            count = self._completed[env_name]
            total = self._totals[env_name]
            self._progress.update(
                self._tasks[env_name],
                completed=count,
                description=self._fmt_desc("", env_name, count, total, package_name),
            )

    def complete_env(self, env_name: str) -> None:
        """Mark an environment as fully complete.

        :param env_name: Short environment name
        """
        if env_name in self._tasks:
            total = self._totals[env_name]
            self._progress.update(
                self._tasks[env_name],
                completed=total,
                description=self._fmt_desc(f"{CHECKMARK} ", env_name, total, total),
            )

    def fail_env(self, env_name: str, reason: str) -> None:
        """Mark an environment as failed.

        :param env_name: Short environment name
        :param reason: Failure reason
        """
        if env_name in self._tasks:
            total = self._totals[env_name]
            count = self._completed.get(env_name, 0)
            self._progress.update(
                self._tasks[env_name],
                completed=total,
                description=f"  {CROSS} [cyan]{env_name.ljust(self._max_env_len)}[/cyan]  {count}/{total}  [red]{reason}[/red]",
            )

    def finish(self) -> None:
        """Stop the progress display."""
        self._progress.stop()


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

        self.console.print(f"{CHECKMARK} {description} [dim]{summary}[/dim]")

    def show_download_progress(self, specs: List[str], label: str = "Downloading") -> DownloadTracker:
        """Show a single progress bar for downloading with active-package display.

        :param specs: List of package specs to download
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
        live = Live(progress, console=self.console, refresh_per_second=8)
        live.start()
        return DownloadTracker(live, progress, task_id, len(specs))

    def show_install_progress(self, specs: List[str], label: str = "Installing") -> DownloadTracker:
        """Show a single progress bar for installing with active-package display.

        :param specs: List of package specs to install
        :param label: Label for the progress bar
        :returns: DownloadTracker for updating progress
        """
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            console=self.console,
        )
        task_id = progress.add_task(f"  {label}", total=len(specs))
        live = Live(progress, console=self.console, refresh_per_second=8)
        live.start()
        return DownloadTracker(live, progress, task_id, len(specs))

    def show_group_install_progress(
        self, env_names: List[str], env_totals: Dict[str, int]
    ) -> GroupInstallTracker:
        """Show per-environment install progress bars.

        :param env_names: Ordered list of short environment names
        :param env_totals: Dict mapping env name to total package count
        :returns: GroupInstallTracker for updating progress
        """
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
            transient=False,
        )
        progress.start()
        tasks = {}
        max_env_len = max((len(n) for n in env_names), default=4)
        for name in env_names:
            total = env_totals.get(name, 0)
            padded = name.ljust(max_env_len)
            task_id = progress.add_task(f"  [cyan]{padded}[/cyan]  0/{total}", total=total)
            tasks[name] = task_id
        return GroupInstallTracker(progress, tasks, env_totals)
