"""Upgrade UI display layer using Rich progress components."""

from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn


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
