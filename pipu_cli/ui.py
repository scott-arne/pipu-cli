"""Upgrade UI display layer using Rich progress components."""

from typing import Dict, List, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TaskID


class PackageTracker:
    """Tracks per-package progress for download or install phases."""

    CHECKMARK = "[bold green]\u2713[/bold green]"
    CROSS = "[bold red]\u2717[/bold red]"

    def __init__(self, progress: Progress, tasks: Dict[str, TaskID]) -> None:
        self._progress = progress
        self._tasks = tasks

    def complete(self, spec: str) -> None:
        """Mark a package as complete.

        :param spec: Package spec (e.g., "requests==2.31.0")
        """
        if spec in self._tasks:
            self._progress.update(self._tasks[spec], completed=1, description=f"{self.CHECKMARK} {spec}")

    def fail(self, spec: str, reason: str) -> None:
        """Mark a package as failed.

        :param spec: Package spec
        :param reason: Failure reason
        """
        if spec in self._tasks:
            self._progress.update(self._tasks[spec], completed=1, description=f"{self.CROSS} {spec} [red]({reason})[/red]")

    def finish(self) -> None:
        """Stop the progress display."""
        self._progress.stop()


class GroupInstallTracker:
    """Tracks per-environment install progress with parallel bars."""

    CHECKMARK = "[bold green]\u2713[/bold green]"
    CROSS = "[bold red]\u2717[/bold red]"

    def __init__(self, progress: Progress, tasks: Dict[str, TaskID], totals: Dict[str, int]) -> None:
        self._progress = progress
        self._tasks = tasks
        self._totals = totals
        self._completed: Dict[str, int] = {name: 0 for name in tasks}

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
                description=f"  [cyan]{env_name}[/cyan]  {count}/{total}  [dim]{package_name}[/dim]",
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
                description=f"  {self.CHECKMARK} [cyan]{env_name}[/cyan]  {total}/{total}",
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
                description=f"  {self.CROSS} [cyan]{env_name}[/cyan]  {count}/{total}  [red]{reason}[/red]",
            )

    def finish(self) -> None:
        """Stop the progress display."""
        self._progress.stop()


class UpgradeUI:
    """Manages upgrade command display: spinner/checkmark phases and progress trackers."""

    CHECKMARK = "[bold green]\u2713[/bold green]"

    def __init__(self, console: Console) -> None:
        """Initialize with a Rich console.

        :param console: Rich Console for output
        """
        self.console = console
        self._active_phase: Optional[Progress] = None
        self._active_task_id: Optional[int] = None
        self._active_description: Optional[str] = None

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

        self.console.print(f"{self.CHECKMARK} {description} [dim]{summary}[/dim]")

    def show_download_progress(self, specs: List[str]) -> PackageTracker:
        """Show multi-task download progress.

        :param specs: List of package specs to download
        :returns: PackageTracker for updating progress
        """
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=False,
        )
        progress.start()
        tasks = {}
        for spec in specs:
            task_id = progress.add_task(f"  {spec}", total=1)
            tasks[spec] = task_id
        return PackageTracker(progress, tasks)

    def show_install_progress(self, specs: List[str]) -> PackageTracker:
        """Show multi-task install progress.

        :param specs: List of package specs to install
        :returns: PackageTracker for updating progress
        """
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=False,
        )
        progress.start()
        tasks = {}
        for spec in specs:
            task_id = progress.add_task(f"  {spec}", total=1)
            tasks[spec] = task_id
        return PackageTracker(progress, tasks)

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
        for name in env_names:
            total = env_totals.get(name, 0)
            task_id = progress.add_task(f"  [cyan]{name}[/cyan]  0/{total}", total=total)
            tasks[name] = task_id
        return GroupInstallTracker(progress, tasks, env_totals)
