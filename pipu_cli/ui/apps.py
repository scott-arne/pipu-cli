"""
Main application classes for the TUI interface.

Contains the complete MainTUIApp and PackageSelectionApp implementations
with full feature sets and functionality.
"""

from typing import List, Dict, Any, Tuple, cast, Optional
import logging
import subprocess
import sys
import time
import signal
import atexit
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid, ScrollableContainer
from textual.widgets import Header, Footer, DataTable, Button, Static, Input, Label
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.worker import get_current_worker
from textual.errors import NoWidget
from textual.coordinate import Coordinate
from rich.text import Text
from ..internals import _check_constraint_satisfaction, list_outdated, get_constraint_color
from ..package_constraints import add_constraints_to_config, read_constraints, read_ignores
from pip._internal.metadata import get_default_environment

# Import modular components
from .constants import (
    COLUMN_SELECTION, COLUMN_PACKAGE, COLUMN_CURRENT, COLUMN_LATEST,
    COLUMN_TYPE, COLUMN_CONSTRAINT, COLUMN_INVALID_WHEN,
    FORCE_EXIT_TIMEOUT, UNINSTALL_TIMEOUT
)
from .modal_dialogs import (
    ConstraintInputScreen, HelpScreen,
    DeleteConstraintConfirmScreen, RemoveAllConstraintsConfirmScreen,
    UninstallConfirmScreen, UpdateConfirmScreen, PackageUpdateScreen,
    NetworkErrorScreen
)
from .table_widgets import PackageSelectionTable

# Set up module logger
logger = logging.getLogger(__name__)


def _restore_terminal() -> None:
    """
    Restore terminal to normal mode in case of unclean exit.

    This prevents the terminal from being left in raw mode or alternate screen mode
    which can cause control characters to be displayed instead of being interpreted.

    Uses the centralized safe_terminal_reset utility for cross-platform compatibility.
    """
    from ..utils import safe_terminal_reset
    safe_terminal_reset()


def _setup_signal_handlers() -> None:
    """
    Set up signal handlers to ensure clean terminal restoration on exit.

    Handles SIGINT (Ctrl+C), SIGTERM, and other termination signals to ensure
    the terminal is properly restored even if the application is forcibly terminated.
    """
    def signal_handler(signum, frame):
        """Handle termination signals by restoring terminal and exiting."""
        del signum, frame  # Unused parameters
        _restore_terminal()
        sys.exit(0)

    # Register signal handlers for clean exit
    try:
        signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # Termination
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal_handler)   # Hangup (Unix)
    except Exception:
        # Signal handling may not be available on all platforms
        pass

    # Also register atexit handler as final fallback
    atexit.register(_restore_terminal)


class PackageSelectionApp(App):
    """Textual app for interactive package selection."""

    BINDINGS = [
        Binding("h", "show_help", "Help", show=True),
        Binding("escape,q", "quit_app", "Quit", show=True),
    ]

    CSS = """
    Screen {
        layers: base overlay;
    }

    Header {
        dock: top;
        height: 3;
    }

    Footer {
        dock: bottom;
        height: 3;
    }

    #info-panel {
        height: 3;
        dock: top;
        background: $panel;
        color: $text;
        text-align: center;
    }

    #selection-info {
        height: 2;
        dock: bottom;
        background: $panel;
        color: $text;
        text-align: center;
    }

    PackageSelectionTable {
        border: solid $primary;
        margin: 1;
    }

    PackageSelectionTable > .datatable--cursor {
        background: $primary 20%;
    }

    PackageSelectionTable > .datatable--header {
        background: $surface;
        color: $text;
        text-style: bold;
    }

    #button-container > Button {
        margin: 1;
        width: 20;
        height: 3;
    }

    #button-container {
        height: 4;
        align: center middle;
        margin-bottom: 2;
    }
    """

    def __init__(self, outdated_packages: List[Dict[str, Any]]):
        """Initialize the package selection app."""
        super().__init__()
        self.outdated_packages = outdated_packages
        self.selected_packages = []
        self.confirmed = False

        # Set up terminal cleanup handlers
        _setup_signal_handlers()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Static(
            f"Found {len(self.outdated_packages)} outdated packages.\n"
            f"Select packages to update using SPACE, then press ENTER to confirm.",
            id="info-panel"
        )

        yield PackageSelectionTable(self.outdated_packages, id="package-table")

        with Horizontal(id="button-container"):
            yield Button("Update Selected", id="update-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn", variant="error")

        yield Static("", id="selection-info")

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        self._update_selection_info()

    def on_package_selection_table_selection_changed(self, message: PackageSelectionTable.SelectionChanged) -> None:
        """Handle package selection change."""
        self._update_selection_info(message.selected_count, message.total_count)

    def on_package_selection_table_confirm_selection(self, message: PackageSelectionTable.ConfirmSelection) -> None:
        """Handle selection confirmation."""
        self.selected_packages = message.selected_packages
        self.confirmed = True
        self.exit()

    def _update_selection_info(self, selected_count: int = 0, total_count: int = 0) -> None:
        """Update the selection info display."""
        info_widget = self.query_one("#selection-info", Static)
        info_widget.update(f"Selected {selected_count} of {total_count} packages")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "update-btn":
            # Get selected packages from table
            table = self.query_one("#package-table", PackageSelectionTable)
            self.selected_packages = [
                pkg for pkg in self.outdated_packages
                if table.selected_packages.get(pkg['name'], False)
            ]
            self.confirmed = True
            self.exit()
        elif event.button.id == "cancel-btn":
            self.confirmed = False
            self.exit()

    def action_show_help(self) -> None:
        """Show help screen."""
        self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        """Quit the application."""
        # Ensure terminal is restored to normal mode
        _restore_terminal()
        self.confirmed = False
        self.exit()


class MainTUIApp(App):
    """Main TUI application that shows all packages and async checks for updates."""

    BINDINGS = [
        Binding("c", "add_constraint", "Add Constraint", show=True),
        Binding("f", "filter_outdated", "F: filter to outdated", show=True),
        Binding("s", "show_all", "S: show all", show=True),
        Binding("u", "update_selected", "U: update selected", show=True),
        Binding("x", "uninstall_package", "X: uninstall", show=True),
        Binding("d", "delete_constraint", "Delete Constraint", show=True),
        Binding("r", "remove_all_constraints", "Remove All Constraints", show=True),
        Binding("h", "show_help", "Help", show=True),
        Binding("escape,q,ctrl+q,ctrl+c", "quit_app", "Quit", show=True),
        Binding("enter", "handle_enter", "", show=False),  # Hidden binding for conditional Enter behavior
    ]

    CSS = """
    Screen {
        layers: base overlay;
    }

    Header {
        dock: top;
        height: 3;
    }

    Footer {
        dock: bottom;
        height: 3;
    }

    #header-stack {
        dock: top;
        height: 3;
    }

    #info-panel {
        height: 2;
        background: $panel;
        color: $text;
        text-align: center;
    }

    #filter-mode-container {
        height: 1;
        background: $panel;
        align: center middle;
        padding: 0 1;
    }

    #filter-label {
        width: auto;
        text-style: bold;
        margin-right: 1;
    }

    #filter-description {
        width: auto;
        text-style: italic;
        color: $text-muted;
        margin-left: 1;
    }

    #custom-footer {
        height: 1;
        dock: bottom;
        background: $panel;
        color: $text;
        text-align: center;
        padding: 0 1;
    }

    #main-table {
        border: solid $primary;
        margin: 1;
        margin-bottom: 2;
    }

    #main-table > .datatable--cursor {
        background: $primary 20%;
    }

    #main-table > .datatable--header {
        background: $surface;
        color: $text;
        text-style: bold;
    }

    #button-container > Button {
        margin: 1;
        width: 20;
        height: 3;
    }

    #button-container {
        height: 4;
        align: center middle;
        margin-bottom: 2;
    }
    """

    def __init__(self):
        """Initialize the main TUI app."""
        super().__init__()
        self.all_packages = []
        self.outdated_packages = []
        self.update_check_complete = True  # Start as True, will be set to False when checking begins
        self.update_check_successful = False  # Track if the check completed successfully
        self.constraints = {}
        self.ignores = set()
        self.invalidation_triggers = {}
        self.filter_outdated_only = False  # Default to showing all packages
        self.package_row_mapping = {}  # Maps package name to row index for efficient updates

        # Set up terminal cleanup handlers
        _setup_signal_handlers()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        # Use a vertical container to properly stack the header elements
        with Vertical(id="header-stack"):
            yield Static(
                "pipu - Package Management\n"
                "Space: toggle, U: update, H: help, Q: quit",
                id="info-panel"
            )

            # Filter mode indicator - third line
            with Horizontal(id="filter-mode-container"):
                yield Static("Filter Mode:", id="filter-label")
                yield Static("Show all packages", id="filter-description")

        yield DataTable(id="main-table", cursor_type="row")

        with Horizontal(id="button-container"):
            yield Button("Update Selected", id="update-btn", variant="primary")
            yield Button("Quit", id="quit-btn", variant="error")

        # Custom footer with status
        yield Static(
            "Ready to load packages",
            id="custom-footer"
        )

    def on_mount(self) -> None:
        """Set up the app when mounted."""
        # Load constraints and ignores
        self.constraints = read_constraints()
        self.ignores = read_ignores()

        # Load invalidation triggers
        self.invalidation_triggers = self._load_invalidation_triggers()

        # Load all installed packages first
        self._load_installed_packages()

        # Start async outdated package checking using a timer
        self.call_later(self._start_update_check, 0.5)

    def _load_invalidation_triggers(self) -> Dict[str, List[str]]:
        """Load invalidation triggers from pip configuration and auto-discovered constraints."""
        from ..package_constraints import read_invalidation_triggers
        return read_invalidation_triggers()

    def on_unmount(self) -> None:
        """Clean up when the app is unmounting."""
        # Ensure terminal is restored to normal mode
        _restore_terminal()

        # Cancel all workers immediately
        try:
            for worker in self.workers:
                worker.cancel()
        except Exception:
            pass

    def _load_installed_packages(self) -> None:
        """Load all installed packages into the table."""
        # Update status to show we're loading packages
        self._update_status("Loading installed packages...", False)

        try:
            env = get_default_environment()
            installed_dists = env.iter_all_distributions()

            # Detect editable packages for display and preservation
            from ..internals import get_editable_packages
            editable_packages = get_editable_packages()

            # Set up the data table (if app is mounted)
            table = None
            try:
                table = self.query_one("#main-table", DataTable)
                table.add_column("Sel", width=4)
                table.add_column("Package", width=20)
                table.add_column("Current", width=12)
                table.add_column("Latest", width=12)
                table.add_column("Type", width=8)
                table.add_column("Constraint", width=20)
                table.add_column("Invalid When", width=25)
            except Exception:
                # App not mounted or table not available (e.g., during testing)
                pass

            # Load all packages
            self.all_packages = []
            for dist in installed_dists:
                try:
                    package_name = dist.metadata["name"]
                    current_version = str(dist.version)

                    # Normalize package name for constraint and trigger lookups
                    from packaging.utils import canonicalize_name
                    canonical_name = canonicalize_name(package_name)

                    # Get invalidation triggers for this package
                    package_triggers = self.invalidation_triggers.get(canonical_name, [])
                    invalid_when_display = ", ".join(package_triggers) if package_triggers else None

                    package_info = {
                        "name": package_name,
                        "version": current_version,
                        "latest_version": "Checking...",
                        "latest_filetype": "",
                        "constraint": self.constraints.get(canonical_name),
                        "invalid_when": invalid_when_display,
                        "selected": False,
                        "outdated": False,
                        "editable": canonical_name in editable_packages
                    }

                    self.all_packages.append(package_info)

                except Exception:
                    continue

            # Sort packages alphabetically
            self.all_packages.sort(key=lambda x: x["name"].lower())

            # Add sorted packages to table and build row mapping (if table exists)
            self.package_row_mapping = {}
            if table is not None:
                # In filtered mode, start with an empty table that gets populated as outdated packages are discovered
                # In show-all mode, populate with all packages initially
                if not self.filter_outdated_only:
                    for i, package_info in enumerate(self.all_packages):
                        constraint_display = package_info.get('constraint', '')
                        if constraint_display:
                            constraint_text = Text.from_markup(f"[yellow]{constraint_display}[/yellow]")
                        else:
                            constraint_text = Text.from_markup("[dim]-[/dim]")

                        # Format invalid when display
                        invalid_when = package_info.get('invalid_when')
                        if invalid_when:
                            invalid_when_text = Text.from_markup(f"[yellow]{invalid_when}[/yellow]")
                        else:
                            invalid_when_text = Text.from_markup("[dim]-[/dim]")

                        # Format selection indicator (initially unselected)
                        selection_text = Text(" ", style="dim")

                        # Format package name with editable indicator
                        if package_info.get("editable", False):
                            package_display = Text.from_markup(f"[bold cyan]{package_info['name']}[/bold cyan] [dim]📝[/dim]")
                        else:
                            package_display = package_info["name"]

                        table.add_row(
                            selection_text,
                            package_display,
                            package_info["version"],
                            "Checking...",
                            "",
                            constraint_text,
                            invalid_when_text,
                            key=package_info["name"]
                        )
                        self.package_row_mapping[package_info["name"]] = i
                # In filtered mode, table starts empty - packages will be added via _update_package_result as they're discovered to be outdated
            else:
                # If no table, just build the row mapping for all packages
                for i, package_info in enumerate(self.all_packages):
                    self.package_row_mapping[package_info["name"]] = i

            # Update status to show packages loaded
            self._update_status("Packages loaded. Checking for updates...", False)

        except Exception as e:
            self.notify(f"Error loading packages: {e}")

    def _start_update_check(self, *args) -> None:
        """Start the update check using a worker."""
        import threading
        del args  # Unused parameter
        self.update_check_complete = False
        self._set_update_button_enabled(False)
        # Create a cancellation event for this check
        self.update_check_cancel_event = threading.Event()
        self.run_worker(self._check_outdated_packages, thread=True, exclusive=True, name="outdated_check")

    def _check_outdated_packages(self) -> None:
        """Worker function to check for outdated packages."""
        import threading

        try:
            worker = get_current_worker()
            if worker.is_cancelled:
                return
        except Exception:
            # No active worker (e.g., during testing)
            worker = None

        try:
            self.call_from_thread(self._update_status, "Checking for package updates...", True)
        except Exception:
            # Not in app context (e.g., during testing)
            pass

        try:
            # Define progress callback to update status
            def progress_callback(package_name: str):
                if worker and worker.is_cancelled:
                    return
                try:
                    self.call_from_thread(self._update_status, f"Checking {package_name}...", True)
                except Exception:
                    pass

            # Define result callback to update individual table rows
            def result_callback(package_result: Dict[str, Any]):
                if worker and worker.is_cancelled:
                    return
                try:
                    self.call_from_thread(self._update_package_result, package_result)
                except Exception:
                    pass

            # Create a silent console to avoid interference with TUI
            from rich.console import Console
            import io
            silent_console = Console(file=io.StringIO(), width=120)

            # Use list_outdated with silent console to avoid interference with TUI
            outdated_packages = list_outdated(
                console=silent_console,
                print_table=False,
                constraints=self.constraints,
                ignores=self.ignores,
                pre=False,
                progress_callback=progress_callback,
                result_callback=result_callback,
                cancel_event=self.update_check_cancel_event
            )

            if not worker or not worker.is_cancelled:
                try:
                    self.call_from_thread(self._update_outdated_results, outdated_packages)
                except Exception:
                    pass

        except ConnectionError as e:
            # Network connectivity issue - show error dialog and exit
            if not worker or not worker.is_cancelled:
                try:
                    error_msg = str(e)
                    self.call_from_thread(self._handle_update_check_error, error_msg, is_network_error=True)
                except Exception:
                    pass
        except Exception as e:
            # Other errors during update check
            if not worker or not worker.is_cancelled:
                try:
                    error_msg = f"Error checking for updates: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    self.call_from_thread(self._handle_update_check_error, error_msg, is_network_error=False)
                except Exception:
                    pass

    def _update_status(self, message: str, show_spinner: bool = False) -> None:
        """Update the status message."""
        try:
            footer_widget = self.query_one("#custom-footer", Static)
            spinner_text = "⟳ " if show_spinner else ""
            footer_widget.update(f"{spinner_text}{message}")
        except Exception:
            # Fallback to notification if status widget fails
            self.notify(f"{message}")

    def _handle_update_check_error(self, error_message: str, is_network_error: bool = False) -> None:
        """Handle errors that occur during the update check process."""
        # Mark check as complete but unsuccessful
        self.update_check_complete = True
        self.update_check_successful = False
        self._set_update_button_enabled(False)

        # Update status to show error
        self._update_status(f"❌ {error_message} - Press Enter or Escape to exit", False)

        # Show notification
        self.notify(f"Update check failed: {error_message}. Press Enter or Escape to exit.", severity="error")

        # For network errors, show the modal dialog and exit
        if is_network_error:
            self.push_screen(NetworkErrorScreen(error_message))

    def _show_network_error_and_exit(self, error_message: str) -> None:
        """Show network error dialog and exit when dismissed."""
        self.push_screen(NetworkErrorScreen(error_message))

    def _update_package_result(self, package_result: Dict[str, Any]) -> None:
        """Update a single package row with its result."""
        try:
            package_name = package_result["name"]
            latest_version = package_result["latest_version"]
            filetype = package_result["latest_filetype"] or ""
            current_version = package_result["version"]

            # First, update the package data in all_packages (this is the source of truth)
            pkg_data = None
            for pkg in self.all_packages:
                if pkg["name"] == package_name:
                    pkg["latest_version"] = latest_version
                    pkg["latest_filetype"] = filetype
                    pkg["outdated"] = (latest_version != current_version)

                    # Auto-select packages that can be updated without constraint conflicts
                    if pkg["outdated"]:
                        constraint = pkg.get('constraint')
                        if constraint:
                            # Check if the latest version satisfies the constraint
                            selected = _check_constraint_satisfaction(latest_version, constraint)
                        else:
                            # No constraint, can be updated freely
                            selected = True
                        pkg["selected"] = selected
                    else:
                        # Package is up to date, don't select it
                        pkg["selected"] = False

                    pkg_data = pkg
                    break

            if not pkg_data:
                # Package not found in all_packages, skip
                return

            # Check if this package should be visible in current filter mode
            should_be_visible = True
            if self.filter_outdated_only:
                # In filtered mode, only show packages that have been confirmed as outdated
                # Don't show packages that are still being checked or up-to-date
                should_be_visible = pkg_data["outdated"]

            # Check if package is currently visible in the table
            row_index = self.package_row_mapping.get(package_name)
            is_currently_visible = row_index is not None

            # Determine if we need to refresh the entire table
            needs_refresh = False
            if self.filter_outdated_only:
                # If package visibility changed, we need to refresh
                if should_be_visible != is_currently_visible:
                    needs_refresh = True

            if needs_refresh:
                # Full table refresh needed due to filtering changes
                # Preserve cursor position during real-time filtering
                self._refresh_table_display(preserve_cursor=True)
            elif is_currently_visible and should_be_visible:
                # Package is visible and should remain visible - update the row in place
                table = self.query_one("#main-table", DataTable)

                # Validate that the row index is still valid
                if row_index >= len(table.rows) or row_index < 0:
                    # Row mapping is stale, do a full refresh
                    # Preserve cursor position when doing emergency refresh
                    self._refresh_table_display(preserve_cursor=True)
                    return

                # Color code the latest version based on update status
                if latest_version == current_version:
                    # Package is up-to-date, show in default color
                    latest_display = latest_version
                    type_display = ""  # Empty type column for current packages
                else:
                    # Package is outdated - check if it can be updated
                    constraint = pkg_data.get('constraint')
                    # Use utility method for consistent formatting
                    latest_display = self._format_latest_version(latest_version, constraint)
                    type_display = filetype

                # Update the table cells
                try:
                    # Update selection indicator
                    if pkg_data.get("selected", False):
                        selection_text = Text("●", style="green bold")
                    else:
                        selection_text = Text(" ", style="dim")

                    table.update_cell_at(cast(Coordinate, (row_index, COLUMN_SELECTION)), selection_text)
                    table.update_cell_at(cast(Coordinate, (row_index, COLUMN_LATEST)), latest_display)
                    table.update_cell_at(cast(Coordinate, (row_index, COLUMN_TYPE)), type_display)
                except Exception:
                    # Coordinates are invalid, do a full refresh
                    # Preserve cursor position when doing emergency refresh
                    self._refresh_table_display(preserve_cursor=True)

        except Exception:
            # Log error for debugging but don't crash the app - make it less verbose
            pass  # Silent failure for coordinate errors

    def _format_latest_version(self, latest_version: str, constraint: Optional[str]) -> Text:
        """
        Format latest version with conditional coloring based on constraint satisfaction.

        :param latest_version: The latest version string
        :param constraint: Optional constraint specification
        :returns: Text object with appropriate color markup
        """
        color = get_constraint_color(latest_version, constraint)
        return Text.from_markup(f"[{color}]{latest_version}[/{color}]")

    def _get_selected_package(self) -> dict | None:
        """
        Get the complete package data for the currently selected row in the table.

        This method properly handles the table cursor and filtering to return
        the correct package data, avoiding RowKey object issues.

        :returns: Package dictionary or None if no valid selection
        """
        table = self.query_one("#main-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(table.rows):
            return None

        # Filter packages based on current display settings
        packages_to_show = []
        for pkg in self.all_packages:
            if self.filter_outdated_only:
                if pkg.get("outdated", False):
                    packages_to_show.append(pkg)
            else:
                packages_to_show.append(pkg)

        # Sort to match table order
        packages_to_show.sort(key=lambda x: x["name"].lower())

        # Get the package at the cursor position
        if table.cursor_row < len(packages_to_show):
            return packages_to_show[table.cursor_row]

        return None

    def _refresh_table_display(self, preserve_cursor: bool = False) -> None:
        """Refresh the table display based on current filter settings."""
        try:
            table = self.query_one("#main-table", DataTable)

            # Save cursor position and scroll offset if requested
            cursor_row = None
            cursor_package_name = None
            scroll_offset_y = None
            if preserve_cursor:
                if table.cursor_row is not None:
                    cursor_row = table.cursor_row
                    # Try to get the package name at the current cursor position
                    try:
                        if cursor_row < len(table.rows):
                            # Get the package name from the displayed packages list
                            packages_to_show = []
                            for pkg in self.all_packages:
                                if self.filter_outdated_only:
                                    if pkg.get("outdated", False):
                                        packages_to_show.append(pkg)
                                else:
                                    packages_to_show.append(pkg)
                            packages_to_show.sort(key=lambda x: x["name"].lower())

                            if cursor_row < len(packages_to_show):
                                cursor_package_name = packages_to_show[cursor_row]['name']
                    except Exception:
                        pass

                # Save the current scroll position
                try:
                    scroll_offset_y = table.scroll_offset.y
                except Exception:
                    pass

            # Clear only the rows, not the columns
            table.clear(columns=False)

            # Filter packages based on current settings
            packages_to_show = []
            for pkg in self.all_packages:
                if self.filter_outdated_only:
                    # In filtered mode, only show packages that have been confirmed as outdated
                    # Don't show packages that are still being checked ("Checking...") or up-to-date
                    if pkg.get("outdated", False):
                        packages_to_show.append(pkg)
                    # Skip packages with "Checking..." or up-to-date packages
                else:
                    # Show all packages
                    packages_to_show.append(pkg)

            # Ensure packages remain sorted alphabetically
            packages_to_show.sort(key=lambda x: x["name"].lower())

            # Rebuild row mapping for displayed packages
            self.package_row_mapping = {}

            # Add rows to table
            for i, pkg in enumerate(packages_to_show):
                constraint_display = pkg.get('constraint', '')
                if constraint_display:
                    constraint_text = Text.from_markup(f"[yellow]{constraint_display}[/yellow]")
                else:
                    constraint_text = Text.from_markup("[dim]-[/dim]")

                # Format the latest version with color coding based on update status
                latest_version = pkg.get("latest_version", "Checking...")
                if latest_version == "Checking...":
                    # Still checking, show as-is
                    latest_display = latest_version
                    type_display = pkg.get("latest_filetype", "")
                elif latest_version == pkg["version"]:
                    # Package is up-to-date, show in default color
                    latest_display = latest_version
                    type_display = ""  # Empty type column for current packages
                else:
                    # Package is outdated - check if it can be updated
                    constraint = pkg.get('constraint')
                    # Use utility method for consistent formatting
                    latest_display = self._format_latest_version(latest_version, constraint)
                    type_display = pkg.get("latest_filetype", "")

                # Format invalid when display
                invalid_when = pkg.get('invalid_when')
                if invalid_when:
                    invalid_when_text = Text.from_markup(f"[yellow]{invalid_when}[/yellow]")
                else:
                    invalid_when_text = Text.from_markup("[dim]-[/dim]")

                # Format selection indicator
                if pkg.get("selected", False):
                    selection_text = Text("●", style="green bold")  # Selected indicator
                else:
                    selection_text = Text(" ", style="dim")         # Empty space

                # Format package name with editable indicator
                if pkg.get("editable", False):
                    package_display = Text.from_markup(f"[bold cyan]{pkg['name']}[/bold cyan] [dim]📝[/dim]")
                else:
                    package_display = pkg["name"]

                table.add_row(
                    selection_text,
                    package_display,
                    pkg["version"],
                    latest_display,
                    type_display,
                    constraint_text,
                    invalid_when_text,
                    key=pkg["name"]
                )
                # Update row mapping for this package
                self.package_row_mapping[pkg["name"]] = i

            # Restore cursor position and scroll offset if possible
            if preserve_cursor:
                # First restore cursor position
                if cursor_package_name:
                    # Try to find the package in the new table and restore cursor
                    new_row_index = self.package_row_mapping.get(cursor_package_name)
                    if new_row_index is not None:
                        try:
                            table.move_cursor(row=new_row_index)
                        except Exception:
                            pass  # If cursor restoration fails, just continue
                    elif cursor_row is not None:
                        # Package is no longer visible, try to restore to the same row index
                        try:
                            max_row = len(table.rows) - 1
                            if max_row >= 0:
                                restore_row = min(cursor_row, max_row)
                                table.move_cursor(row=restore_row)
                        except Exception:
                            pass

                # Then restore scroll position
                if scroll_offset_y is not None:
                    try:
                        # Schedule scroll restoration to happen after the table is rendered
                        self.set_timer(0.01, lambda: self._restore_scroll_position(table, scroll_offset_y))
                    except Exception:
                        pass

        except Exception:
            pass

    def _restore_scroll_position(self, table, scroll_y: int) -> None:
        """Helper method to restore scroll position after table refresh."""
        try:
            table.scroll_to(y=scroll_y, animate=False)
        except Exception:
            pass  # If scroll restoration fails, just continue

    def _show_uninstall_confirmation(self, package_name: str) -> None:
        """Show confirmation dialog for uninstalling a package."""
        def uninstall_confirmed(confirmed: bool | None) -> None:
            if confirmed:
                self._uninstall_package(package_name)

        self.push_screen(UninstallConfirmScreen(package_name), uninstall_confirmed)

    def _uninstall_package(self, package_name: str) -> None:
        """Actually uninstall the package."""
        self._update_status(f"Uninstalling {package_name}...", True)

        def run_uninstall():
            """Run pip uninstall in a worker thread."""
            try:
                # Use sys.executable to find the correct pip for the current Python environment
                pip_cmd = [sys.executable, "-m", "pip", "uninstall", package_name, "-y"]
                result = subprocess.run(
                    pip_cmd,
                    capture_output=True,
                    text=True,
                    timeout=UNINSTALL_TIMEOUT
                )

                if result.returncode == 0:
                    self.call_from_thread(self.notify, f"Successfully uninstalled {package_name}", "information")
                    self.call_from_thread(self._remove_package_from_table, package_name)
                else:
                    self.call_from_thread(self.notify, f"Failed to uninstall {package_name}: {result.stderr}", "error")
            except subprocess.TimeoutExpired:
                self.call_from_thread(self.notify, f"Uninstall of {package_name} timed out", "error")
            except Exception as e:
                self.call_from_thread(self.notify, f"Error uninstalling {package_name}: {e}", "error")
            finally:
                self.call_from_thread(self._update_status, "Ready", False)

        self.run_worker(run_uninstall, thread=True, exclusive=False)

    def _remove_package_from_table(self, package_name: str) -> None:
        """Remove a package from the table after uninstall."""
        try:
            table = self.query_one("#main-table", DataTable)
            if package_name in table.rows:
                table.remove_row(package_name)

            # Remove from our data structures
            self.all_packages = [pkg for pkg in self.all_packages if pkg["name"] != package_name]
            self.outdated_packages = [pkg for pkg in self.outdated_packages if pkg["name"] != package_name]
        except Exception:
            pass

    def _update_outdated_results(self, outdated_packages: List[Dict[str, Any]]) -> None:
        """Update the table with outdated package results."""
        self.outdated_packages = outdated_packages
        self.update_check_complete = True
        self.update_check_successful = True  # Mark as successful
        self._set_update_button_enabled(True)

        # Create a mapping for quick lookup
        outdated_map = {pkg['name'].lower(): pkg for pkg in outdated_packages}

        # Update package data first (source of truth)
        for package in self.all_packages:
            package_name_lower = package['name'].lower()

            if package_name_lower in outdated_map:
                # Package is outdated
                outdated_info = outdated_map[package_name_lower]
                package['outdated'] = True
                package['latest_version'] = outdated_info['latest_version']
                package['latest_filetype'] = outdated_info['latest_filetype']

                # Auto-select if no constraint or constraint is satisfied
                constraint = package.get('constraint')
                if constraint:
                    selected = _check_constraint_satisfaction(outdated_info['latest_version'], constraint)
                else:
                    selected = True

                package['selected'] = selected

            else:
                # Package is up to date
                package['outdated'] = False
                package['latest_version'] = package['version']
                package['latest_filetype'] = ""
                package['selected'] = False

        # Now update the visible table display based on current filter
        # This handles the row mapping correctly and ensures table consistency
        self._refresh_table_display()

        # Update status
        outdated_count = len(outdated_packages)
        selected_count = sum(1 for pkg in self.all_packages if pkg.get('selected', False))

        if outdated_count > 0:
            self._update_status(f"Found {outdated_count} outdated packages, {selected_count} selected for update", False)
        else:
            self._update_status("All packages are up to date!", False)

    def action_quit_app(self) -> None:
        """Quit the application."""
        # Signal cancellation to the update check if it's running
        if hasattr(self, 'update_check_cancel_event') and self.update_check_cancel_event:
            self.update_check_cancel_event.set()

        # Cancel any running workers before exiting with proper tracking
        cancelled_workers = []
        failed_workers = []

        for worker in self.workers:
            if not worker.is_finished:
                try:
                    worker.cancel()
                    cancelled_workers.append(worker.name or "unnamed")
                except Exception as e:
                    logger.error(f"Failed to cancel worker {worker.name or 'unnamed'}: {e}")
                    failed_workers.append(worker.name or "unnamed")

        if cancelled_workers:
            logger.debug(f"Cancelled workers: {', '.join(cancelled_workers)}")
        if failed_workers:
            logger.warning(f"Failed to cancel workers: {', '.join(failed_workers)}")

        # Exit the application
        self.exit()

    def action_handle_enter(self) -> None:
        """Handle Enter key - only quit if update check failed."""
        # Only allow Enter to quit if the update check failed
        if self.update_check_complete and not self.update_check_successful:
            self.exit()
        # Otherwise, do nothing (Enter doesn't quit during normal operation)

    def action_filter_outdated(self) -> None:
        """Set filter to show only outdated packages."""
        if not self.filter_outdated_only:
            self.filter_outdated_only = True
            self._refresh_table_display(preserve_cursor=True)

            # Update the description
            try:
                description = self.query_one("#filter-description", Static)
                description.update("Show outdated only")
            except NoWidget:
                logger.debug("Filter description widget not found - context may not support it")
            except Exception as e:
                logger.warning(f"Could not update filter description: {e}")

            self.notify("Filter: showing only outdated packages")
        else:
            # Already filtering, just acknowledge
            self.notify("Filter: already showing only outdated packages")

    def action_show_all(self) -> None:
        """Set filter to show all packages."""
        if self.filter_outdated_only:
            self.filter_outdated_only = False
            self._refresh_table_display(preserve_cursor=True)

            # Update the description
            try:
                description = self.query_one("#filter-description", Static)
                description.update("Show all packages")
            except NoWidget:
                logger.debug("Filter description widget not found - context may not support it")
            except Exception as e:
                logger.warning(f"Could not update filter description: {e}")

            self.notify("Filter: showing all packages")
        else:
            # Already showing all, just acknowledge
            self.notify("Filter: already showing all packages")

    def action_uninstall_package(self) -> None:
        """Uninstall the currently selected package."""
        selected_package = self._get_selected_package()
        if selected_package:
            self._show_uninstall_confirmation(selected_package['name'])

    def action_show_help(self) -> None:
        """Show the help modal with keyboard shortcuts and features."""
        self.push_screen(HelpScreen())

    def action_add_constraint(self) -> None:
        """Add constraint to the currently selected package."""
        selected_package = self._get_selected_package()
        if not selected_package:
            return

        package_name = selected_package['name']
        current_constraint = selected_package.get('constraint', '')

        def handle_constraint_result(result) -> None:
            """Handle the result from constraint input dialog."""
            if result:
                constraint = ""  # Initialize to prevent unbound variable
                try:
                    # Handle both string (constraint only) and tuple (constraint, trigger) results
                    if isinstance(result, tuple):
                        constraint, invalidation_trigger = result
                    else:
                        constraint = result
                        invalidation_trigger = ""

                    from ..package_constraints import add_constraints_to_config

                    # Add constraint to configuration
                    constraint_spec = f"{package_name}{constraint}"
                    config_path, changes = add_constraints_to_config([constraint_spec])

                    # Add invalidation trigger if provided
                    if invalidation_trigger:
                        from ..package_constraints import format_invalidation_triggers, _get_section_name, _load_config, _write_config_file

                        section_name = _get_section_name(None)
                        config, _ = _load_config(create_if_missing=False)

                        # Format the trigger entry
                        formatted_entry = format_invalidation_triggers(constraint_spec, [invalidation_trigger])
                        if formatted_entry:
                            # Get existing triggers
                            existing_triggers = ""
                            if config.has_option(section_name, 'constraint_invalid_when'):
                                existing_triggers = config.get(section_name, 'constraint_invalid_when')

                            # Add the new trigger
                            if existing_triggers.strip():
                                triggers_value = f"{existing_triggers},{formatted_entry}"
                            else:
                                triggers_value = formatted_entry

                            config.set(section_name, 'constraint_invalid_when', triggers_value)
                            _write_config_file(config, config_path)

                    # Update the package data in all_packages
                    for pkg in self.all_packages:
                        if pkg['name'] == package_name:
                            pkg['constraint'] = constraint
                            if invalidation_trigger:
                                pkg['invalid_when'] = invalidation_trigger
                            break

                    # Refresh the table display
                    self._refresh_table_display(preserve_cursor=True)

                    # Show success message
                    change_type, old_constraint = changes.get(package_name.lower(), ('added', None))
                    if change_type == 'updated':
                        message = f"Updated constraint for {package_name}: {old_constraint} → {constraint}"
                    elif change_type == 'added':
                        message = f"Added constraint {package_name}{constraint}"
                    else:
                        message = f"Constraint {package_name}{constraint} already exists"

                    if invalidation_trigger:
                        message += f" with invalidation trigger: {invalidation_trigger}"
                    self.notify(message)

                except Exception as e:
                    error_msg = str(e)
                    if "Invalid constraint specification" in error_msg:
                        self.notify(f"Invalid constraint '{constraint}' for {package_name}. Try formats like: >=1.0.0, <2.0, ==1.5.0, >1.0")
                    else:
                        self.notify(f"Error adding constraint: {e}")

        # Show constraint input screen
        self.push_screen(
            ConstraintInputScreen(package_name, current_constraint),
            handle_constraint_result
        )

    def action_delete_constraint(self) -> None:
        """Delete constraint for the currently selected package."""
        selected_package = self._get_selected_package()
        if not selected_package:
            return

        package_name = selected_package['name']
        current_constraint = selected_package.get('constraint')

        if not current_constraint:
            self.notify(f"No constraint to delete for {package_name}")
            return

        def handle_delete_confirmation(confirmed: bool | None) -> None:
            """Handle the result from delete constraint confirmation."""
            if confirmed:
                try:
                    from ..package_constraints import remove_constraints_from_config

                    # Remove constraint from configuration
                    _, removed_constraints, removed_triggers = remove_constraints_from_config([package_name])

                    if package_name.lower() in removed_constraints:
                        # Update the package data in all_packages
                        for pkg in self.all_packages:
                            if pkg['name'] == package_name:
                                pkg.pop('constraint', None)
                                pkg.pop('invalid_when', None)
                                break

                        # Refresh the table display
                        self._refresh_table_display(preserve_cursor=True)

                        # Show success message
                        trigger_count = len(removed_triggers.get(package_name.lower(), []))
                        if trigger_count > 0:
                            self.notify(f"Deleted constraint and {trigger_count} invalidation triggers for {package_name}")
                        else:
                            self.notify(f"Deleted constraint for {package_name}")
                    else:
                        self.notify(f"No constraint found for {package_name} in configuration")

                except Exception as e:
                    self.notify(f"Error deleting constraint: {e}")

        # Show confirmation dialog
        self.push_screen(
            DeleteConstraintConfirmScreen(package_name, current_constraint),
            handle_delete_confirmation
        )

    def action_remove_all_constraints(self) -> None:
        """Remove all constraints from the pip configuration."""
        # Count current constraints
        constraint_count = len(self.constraints)

        if constraint_count == 0:
            self.notify("No constraints to remove")
            return

        def handle_remove_all_confirmation(confirmed: bool | None) -> None:
            """Handle the result from remove all constraints confirmation."""
            if confirmed:
                try:
                    from ..package_constraints import _get_section_name, _load_config, _write_config_file

                    # Get config section
                    section_name = _get_section_name(None)
                    config, config_path = _load_config(create_if_missing=False)

                    if not config.has_section(section_name):
                        self.notify("No constraints configuration found")
                        return

                    # Remove all constraint-related options
                    options_removed = []
                    if config.has_option(section_name, 'constraint'):
                        config.remove_option(section_name, 'constraint')
                        options_removed.append('constraints')

                    if config.has_option(section_name, 'constraint_invalid_when'):
                        config.remove_option(section_name, 'constraint_invalid_when')
                        options_removed.append('invalidation triggers')

                    # Remove the section if it's empty
                    if not config.options(section_name):
                        config.remove_section(section_name)

                    # Write the updated config
                    _write_config_file(config, config_path)

                    # Update our internal state
                    self.constraints = {}
                    self.invalidation_triggers = {}

                    # Update all package data
                    for pkg in self.all_packages:
                        pkg.pop('constraint', None)
                        pkg.pop('invalid_when', None)

                    # Refresh the table display
                    self._refresh_table_display(preserve_cursor=True)

                    # Show success message
                    if options_removed:
                        self.notify(f"Removed all {constraint_count} constraints and {' and '.join(options_removed)}")
                    else:
                        self.notify("No constraints were found to remove")

                except Exception as e:
                    self.notify(f"Error removing constraints: {e}")

        # Show confirmation dialog
        self.push_screen(
            RemoveAllConstraintsConfirmScreen(constraint_count),
            handle_remove_all_confirmation
        )

    def _reload_constraints_in_ui(self) -> None:
        """Reload constraints from configuration and update the UI display."""
        try:
            # Reload constraints and invalidation triggers from configuration
            self.constraints = read_constraints()
            self.invalidation_triggers = self._load_invalidation_triggers()

            # Update all packages with new constraint and invalidation trigger information
            for pkg in self.all_packages:
                pkg['constraint'] = self.constraints.get(pkg['name'].lower())

                # Update invalidation triggers
                package_triggers = self.invalidation_triggers.get(pkg['name'].lower(), [])
                pkg['invalid_when'] = ", ".join(package_triggers) if package_triggers else None

            # Refresh table display to show updated constraints
            self._refresh_table_display(preserve_cursor=True)

        except Exception as e:
            self.notify(f"Error reloading constraints in UI: {e}")

    def _set_update_button_enabled(self, enabled: bool) -> None:
        """Enable or disable the Update Selected button."""
        try:
            button = self.query_one("#update-btn", Button)
            button.disabled = not enabled
            if not enabled:
                button.label = "Checking Updates..."
            else:
                button.label = "Update Selected"
        except Exception:
            # Button might not exist or be accessible in all contexts
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "update-btn":
            self.action_update_selected()
        elif event.button.id == "quit-btn":
            self.action_quit_app()

    def action_update_selected(self) -> None:
        """Update selected packages."""
        if not self.update_check_complete:
            self.notify("⏳ Still checking for updates... Press U again once checking is complete", severity="warning")
            return

        # Check if the update check was successful
        if not self.update_check_successful:
            self.notify("❌ Cannot update packages - the update check failed. Please check your network connection and try restarting pipu.", severity="error")
            return

        # Get selected packages (those marked with green dots)
        selected_packages = []

        for pkg in self.all_packages:
            # Check if this package is selected for update
            if pkg.get('selected', False) and pkg.get('outdated', False):
                selected_packages.append(pkg)

        if not selected_packages:
            self.notify("No packages selected for update", severity="warning")
            return

        # Show confirmation dialog with update details
        def handle_update_confirmation(confirmed: bool | None) -> None:
            """Handle the result from update confirmation dialog."""
            logger.info(f"Update confirmation result: {confirmed}")
            if confirmed:
                logger.info(f"Pushing PackageUpdateScreen with {len(selected_packages)} packages")
                # Push the PackageUpdateScreen which handles the full update process
                self.push_screen(PackageUpdateScreen(selected_packages))
                logger.info("PackageUpdateScreen pushed successfully")
            else:
                logger.info("Update cancelled by user")
            # If cancelled, just return without doing anything

        logger.info(f"Showing UpdateConfirmScreen for {len(selected_packages)} packages")
        self.push_screen(UpdateConfirmScreen(selected_packages), handle_update_confirmation)



def main_tui_app() -> None:
    """Launch the main TUI application."""
    # Set up terminal cleanup handlers before starting the app
    _setup_signal_handlers()

    try:
        app = MainTUIApp()
        app.run()
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        _restore_terminal()
        sys.exit(0)
    except Exception as e:
        # Handle any other exceptions
        _restore_terminal()
        logger.error(f"TUI application error: {e}")
        raise
    finally:
        # Always restore terminal state
        _restore_terminal()


def interactive_package_selection(outdated_packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Run interactive package selection using Textual TUI.

    :param outdated_packages: List of outdated package dictionaries
    :returns: List of selected package dictionaries
    """
    if not outdated_packages:
        return []

    # Set up terminal cleanup handlers before starting the app
    _setup_signal_handlers()

    try:
        app = PackageSelectionApp(outdated_packages)
        app.run()

        if app.confirmed:
            return app.selected_packages
        else:
            return []
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        _restore_terminal()
        return []
    except Exception:
        # Handle any other exceptions
        _restore_terminal()
        raise
    finally:
        # Always restore terminal state
        _restore_terminal()