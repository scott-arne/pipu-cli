"""
Table widget components for the TUI interface.

Contains the main package selection table and related functionality.
"""

from typing import List, Dict, Any, cast
import logging
from textual.widgets import DataTable
from textual.binding import Binding
from textual.message import Message
from textual.coordinate import Coordinate
from rich.text import Text
from ..internals import _check_constraint_satisfaction, get_constraint_color, format_invalid_when_display
from ..package_constraints import _get_constraint_invalid_when, _set_constraint_invalid_when
from .constants import (
    COLUMN_SELECTION, COLUMN_CONSTRAINT, COLUMN_INVALID_WHEN
)

# Set up module logger
logger = logging.getLogger(__name__)


class PackageSelectionTable(DataTable):
    """Custom DataTable for package selection with keyboard navigation."""

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle Selection", show=True),
        Binding("enter", "confirm_selection", "Confirm", show=True),
        Binding("c", "add_constraint", "Add Constraint", show=True),
        Binding("shift+a", "select_all", "Select All", show=False),
        Binding("n", "select_none", "Select None", show=False),
        Binding("escape,q", "quit_app", "Quit", show=True),
    ]

    class SelectionChanged(Message):
        """Message sent when package selection changes."""
        def __init__(self, selected_count: int, total_count: int) -> None:
            """
            Initialize selection change message.

            :param selected_count: Number of selected packages
            :param total_count: Total number of packages
            """
            self.selected_count = selected_count
            self.total_count = total_count
            super().__init__()

    class ConfirmSelection(Message):
        """Message sent when user confirms selection."""
        def __init__(self, selected_packages: List[Dict[str, Any]]) -> None:
            """
            Initialize confirm selection message.

            :param selected_packages: List of selected package dictionaries
            """
            self.selected_packages = selected_packages
            super().__init__()

    def __init__(self, outdated_packages: List[Dict[str, Any]], *args, **kwargs):
        """
        Initialize the package selection table.

        :param outdated_packages: List of outdated package dictionaries
        """
        super().__init__(*args, **kwargs)
        self.outdated_packages = outdated_packages
        self.selected_packages = {}
        self._initialize_selection()

    def _initialize_selection(self) -> None:
        """
        Initialize package selection based on constraint satisfaction.

        Packages without constraints are auto-selected. Packages with constraints
        are only selected if their latest version satisfies the constraint.
        """
        for pkg in self.outdated_packages:
            constraint = pkg.get('constraint')
            if constraint:
                # Only pre-select if latest version satisfies constraint
                selected = _check_constraint_satisfaction(pkg['latest_version'], constraint)
            else:
                # No constraint - pre-select by default
                selected = True
            self.selected_packages[pkg['name']] = selected

    def on_mount(self) -> None:
        """
        Set up the data table when mounted.

        Creates columns, adds package rows with selection status,
        and initializes cursor position.
        """
        # Add columns with better widths
        self.add_column("Sel", width=4)
        self.add_column("Package", width=20)
        self.add_column("Current", width=10)
        self.add_column("Latest", width=10)
        self.add_column("Type", width=8)
        self.add_column("Constraint", width=20)
        self.add_column("Constraint Invalid When", width=30)

        # Add rows
        for pkg in self.outdated_packages:
            selected = self.selected_packages[pkg['name']]
            if selected:
                check_symbol = Text("✓", style="bold green")
            else:
                check_symbol = Text(" ", style="dim")

            # Format constraint display
            constraint = pkg.get('constraint')
            if constraint:
                color = get_constraint_color(pkg['latest_version'], constraint)
                constraint_display = f"[{color}]{constraint}[/{color}]"
            else:
                constraint_display = "[dim]-[/dim]"

            # Format invalid when display
            invalid_when = pkg.get('invalid_when')
            invalid_when_display = Text.from_markup(format_invalid_when_display(invalid_when))

            self.add_row(
                check_symbol,
                pkg['name'],
                pkg['version'],
                pkg['latest_version'],
                pkg['latest_filetype'],
                Text.from_markup(constraint_display),
                invalid_when_display,
                key=pkg['name']
            )

        # Set cursor to first row if packages exist
        if self.outdated_packages:
            self.cursor_type = "row"
            self.move_cursor(row=0)

        self._post_selection_change()

    def action_toggle_selection(self) -> None:
        """
        Toggle selection of the current package.

        Updates the visual checkmark and posts a selection change message.
        """
        if self.cursor_row is not None and self.cursor_row < len(self.outdated_packages):
            pkg = self.outdated_packages[self.cursor_row]
            pkg_name = pkg['name']

            # Toggle selection
            self.selected_packages[pkg_name] = not self.selected_packages[pkg_name]

            # Update the check symbol in the table with color
            if self.selected_packages[pkg_name]:
                new_symbol = Text("✓", style="bold green")
            else:
                new_symbol = Text(" ", style="dim")
            self.update_cell_at(cast(Coordinate, (self.cursor_row, COLUMN_SELECTION)), new_symbol)

            self._post_selection_change()

    def action_select_all(self) -> None:
        """
        Select all packages for updating.

        Updates all checkmarks to selected state and posts selection change message.
        """
        for i, pkg in enumerate(self.outdated_packages):
            self.selected_packages[pkg['name']] = True
            self.update_cell_at(cast(Coordinate, (i, COLUMN_SELECTION)), Text("✓", style="bold green"))
        self._post_selection_change()

    def action_select_none(self) -> None:
        """
        Deselect all packages.

        Updates all checkmarks to unselected state and posts selection change message.
        """
        for i, pkg in enumerate(self.outdated_packages):
            self.selected_packages[pkg['name']] = False
            self.update_cell_at(cast(Coordinate, (i, COLUMN_SELECTION)), Text(" ", style="dim"))
        self._post_selection_change()

    def action_confirm_selection(self) -> None:
        """
        Confirm the current selection.

        Posts a message containing the selected packages and exits the interface.
        """
        selected_pkgs = [
            pkg for pkg in self.outdated_packages
            if self.selected_packages[pkg['name']]
        ]
        self.post_message(self.ConfirmSelection(selected_pkgs))

    def action_add_constraint(self) -> None:
        """
        Add constraint to the currently selected package.

        Opens a modal dialog to input constraint specification.
        """
        if self.cursor_row is not None and self.cursor_row < len(self.outdated_packages):
            pkg = self.outdated_packages[self.cursor_row]
            current_constraint = pkg.get('constraint', '')

            def handle_constraint_result(result) -> None:
                """Handle the result from constraint input dialog."""
                if result:
                    try:
                        # Handle both string (constraint only) and tuple (constraint, trigger) results
                        if isinstance(result, tuple):
                            constraint, invalidation_trigger = result
                        else:
                            constraint = result
                            invalidation_trigger = ""

                        # Add constraint to configuration
                        from ..package_constraints import add_constraints_to_config
                        constraint_spec = f"{pkg['name']}{constraint}"
                        config_path, _ = add_constraints_to_config([constraint_spec])

                        # Add invalidation trigger if provided
                        if invalidation_trigger:
                            from ..package_constraints import format_invalidation_triggers, _get_section_name, _load_config, _write_config_file

                            section_name = _get_section_name(None)
                            config, _ = _load_config(create_if_missing=False)

                            # Format the trigger entry
                            formatted_entry = format_invalidation_triggers(constraint_spec, [invalidation_trigger])
                            if formatted_entry:
                                # Get existing triggers
                                existing_triggers = _get_constraint_invalid_when(config, section_name) or ""

                                # Add the new trigger
                                if existing_triggers.strip():
                                    triggers_value = f"{existing_triggers},{formatted_entry}"
                                else:
                                    triggers_value = formatted_entry

                                _set_constraint_invalid_when(config, section_name, triggers_value)
                                _write_config_file(config, config_path)

                        # Update the package data and refresh display
                        pkg['constraint'] = constraint
                        if invalidation_trigger:
                            pkg['invalid_when'] = invalidation_trigger
                        self._refresh_constraint_display(self.cursor_row, pkg)

                        # Show success message
                        message = f"Added constraint {pkg['name']}{constraint}"
                        if invalidation_trigger:
                            message += f" with invalidation trigger: {invalidation_trigger}"
                        self.app.notify(message)

                    except Exception as e:
                        self.app.notify(f"Error adding constraint: {e}")

            # Show constraint input screen
            from .modal_dialogs import ConstraintInputScreen
            self.app.push_screen(
                ConstraintInputScreen(pkg['name'], current_constraint),
                handle_constraint_result
            )

    def action_delete_constraint(self) -> None:
        """
        Delete constraint from the currently selected package.

        Removes the constraint and its invalidation triggers from the pip configuration.
        """
        if self.cursor_row is not None and self.cursor_row < len(self.outdated_packages):
            pkg = self.outdated_packages[self.cursor_row]
            current_constraint = pkg.get('constraint', '')

            if not current_constraint:
                self.app.notify(f"No constraint to delete for {pkg['name']}")
                return

            try:
                from ..package_constraints import remove_constraints_from_config

                # Remove constraint from configuration
                _, removed_constraints, removed_triggers = remove_constraints_from_config([pkg['name']])

                if pkg['name'].lower() in removed_constraints:
                    # Update the package data and refresh display
                    pkg.pop('constraint', None)
                    pkg.pop('invalid_when', None)
                    self._refresh_constraint_display(self.cursor_row, pkg)

                    # Show success message
                    trigger_count = len(removed_triggers.get(pkg['name'].lower(), []))
                    if trigger_count > 0:
                        self.app.notify(f"Deleted constraint and {trigger_count} invalidation triggers for {pkg['name']}")
                    else:
                        self.app.notify(f"Deleted constraint for {pkg['name']}")
                else:
                    self.app.notify(f"No constraint found for {pkg['name']} in configuration")

            except Exception as e:
                self.app.notify(f"Error deleting constraint: {e}")

    def _refresh_constraint_display(self, row: int, pkg: Dict[str, Any]) -> None:
        """
        Refresh the constraint display for a specific package row.

        :param row: Row index to update
        :param pkg: Package data with updated constraint
        """
        constraint = pkg.get('constraint')
        if constraint:
            color = get_constraint_color(pkg['latest_version'], constraint)
            constraint_display = Text.from_markup(f"[{color}]{constraint}[/{color}]")
        else:
            constraint_display = Text.from_markup("[dim]-[/dim]")

        # Update the constraint column (column 5)
        self.update_cell_at(cast(Coordinate, (row, COLUMN_CONSTRAINT)), constraint_display)

        # Update the invalid when column (column 6) if it exists
        invalid_when = pkg.get('invalid_when')
        invalid_when_display = Text.from_markup(format_invalid_when_display(invalid_when))
        self.update_cell_at(cast(Coordinate, (row, COLUMN_INVALID_WHEN)), invalid_when_display)

    def action_quit_app(self) -> None:
        """
        Quit the application.

        Exits the TUI without making any package updates.
        """
        self.app.exit()

    def _post_selection_change(self) -> None:
        """
        Post a message about selection change.

        Counts selected packages and notifies the app about the change.
        """
        selected_count = sum(self.selected_packages.values())
        total_count = len(self.outdated_packages)
        self.post_message(self.SelectionChanged(selected_count, total_count))