"""Upgrade UI display layer using Rich progress components."""

import threading
from typing import Dict, List, Optional, Set

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TaskID
from rich.text import Text


CHECKMARK = "\u2713"
CROSS = "\u2717"
BULLET = "\u25cc"
DOT = "\u00b7"

ENV_NAME_MAX = 16
PKG_NAME_MAX = 20


def _fit(name: str, width: int) -> str:
    """Truncate or pad a string to exactly *width* visible characters."""
    if len(name) <= width:
        return name.ljust(width)
    return name[: width - 1] + "\u2026"


class DownloadTracker:
    """Single progress bar with a compact bulleted list of active downloads below.

    Thread-safe for use with parallel downloads.
    """

    def __init__(self, live: Live, progress: Progress, task_id: TaskID, total: int) -> None:
        self._live = live
        self._progress = progress
        self._task_id = task_id
        self._total = total
        self._completed = 0
        self._failed = 0
        self._active: Set[str] = set()
        self._lock = threading.Lock()

    def start(self, spec: str) -> None:
        """Mark a package as actively downloading.

        :param spec: Package spec (e.g., "requests==2.31.0")
        """
        with self._lock:
            self._active.add(spec)
            self._refresh()

    def complete(self, spec: str) -> None:
        """Mark a package download as complete.

        :param spec: Package spec
        """
        with self._lock:
            self._active.discard(spec)
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
            self._failed += 1
            self._progress.update(self._task_id, completed=self._completed + self._failed)
            self._refresh()

    def _refresh(self) -> None:
        if self._active:
            lines = Text()
            for spec in sorted(self._active):
                lines.append(f"    {BULLET} ", style="dim")
                lines.append(spec, style="dim")
                lines.append("\n")
            if lines.plain.endswith("\n"):
                lines.right_crop(1)
            self._live.update(Group(self._progress, lines))
        else:
            self._live.update(Group(self._progress))

    def finish(self) -> None:
        """Stop the progress display."""
        self._live.update(self._progress)
        self._live.stop()


class GroupInstallTracker:
    """Tracks per-environment install progress with fixed-width aligned bars."""

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

    def _make_desc(self, icon: str, env_name: str) -> str:
        return f"  {icon} [cyan]{_fit(env_name, self._env_width)}[/cyan]"

    def _make_count(self, count: int, total: int) -> str:
        return f"{count}/{total}".rjust(self._count_width)

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
                description=self._make_desc(DOT, env_name),
                count=self._make_count(count, total),
                pkg=_fit(package_name, PKG_NAME_MAX),
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
                description=self._make_desc(f"[bold green]{CHECKMARK}[/bold green]", env_name),
                count=self._make_count(total, total),
                pkg="",
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
                description=self._make_desc(f"[bold red]{CROSS}[/bold red]", env_name),
                count=self._make_count(count, total),
                pkg=f"[red]{reason}[/red]",
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

        self.console.print(f"[bold green]{CHECKMARK}[/bold green] {description} [dim]{summary}[/dim]")

    def show_download_progress(self, specs: List[str], label: str = "Downloading") -> DownloadTracker:
        """Show a single progress bar for downloading with active-package list.

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
        """Show a single progress bar for installing with active-package list.

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
