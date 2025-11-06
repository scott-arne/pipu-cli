"""
Modal dialog classes for the TUI interface.

Contains constraint input, confirmation dialogs, help screen, etc.
"""

from typing import Any, List, Dict, Literal, Optional
import logging
import threading
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Grid, ScrollableContainer
from textual.widgets import Button, Static, Input, Label, DataTable
from textual.screen import ModalScreen
from rich.text import Text

# Set up module logger
logger = logging.getLogger(__name__)

# Type alias for button variants
ButtonVariant = Literal["default", "primary", "success", "warning", "error"]


class BaseConfirmationScreen(ModalScreen[bool]):
    """
    Base class for simple confirmation dialogs with Yes/No or similar buttons.

    This consolidates the common pattern of showing a message and two buttons
    that dismiss with True or False based on the user's choice.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self,
                 message: str,
                 confirm_text: str = "Confirm",
                 cancel_text: str = "Cancel",
                 confirm_variant: ButtonVariant = "success",
                 cancel_variant: ButtonVariant = "primary"):
        """
        Initialize a confirmation dialog.

        :param message: The message or question to display
        :param confirm_text: Text for the confirm button (default "Confirm")
        :param cancel_text: Text for the cancel button (default "Cancel")
        :param confirm_variant: Button variant for confirm (default "success")
        :param cancel_variant: Button variant for cancel (default "primary")
        """
        super().__init__()
        self.message: str = message
        self.confirm_text: str = confirm_text
        self.cancel_text: str = cancel_text
        self.confirm_variant: ButtonVariant = confirm_variant
        self.cancel_variant: ButtonVariant = cancel_variant

    def compose(self) -> ComposeResult:
        """Create the dialog layout."""
        confirm_btn = Button(
            Text(self.confirm_text, style="bold white"),
            id="confirm",
            variant=self.confirm_variant
        )
        cancel_btn = Button(
            Text(self.cancel_text, style="bold white"),
            id="cancel",
            variant=self.cancel_variant
        )

        yield Grid(
            Label(self.message, id="question"),
            Horizontal(confirm_btn, cancel_btn, id="actions"),
            id="dialog"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press - dismiss with True for confirm, False for cancel."""
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        """Handle escape key - dismiss with False."""
        self.dismiss(False)

    CSS = """
    BaseConfirmationScreen {
        align: center middle;
        layer: overlay;
    }

    #dialog {
        grid-size: 1;
        grid-rows: 1fr auto;
        padding: 1 2;
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
    }

    #question {
        height: auto;
        width: 100%;
        content-align: center middle;
        padding: 2 1;
        text-align: center;
    }

    #actions {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #actions Button {
        margin: 0 1;
    }

    /* Explicit button styling for test validation */
    #confirm {
        color: white;
    }

    #cancel {
        background: $primary;
        border: tall $primary;
        color: white;
    }

    Button:focus {
        text-style: bold;
    }
    """


class ConstraintInputScreen(ModalScreen):
    """Modal screen for inputting package constraints."""

    BINDINGS = [
        ("enter", "add_constraint", "Add Constraint"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, package_name: str, current_constraint: str = ""):
        """
        Initialize constraint input screen.

        :param package_name: Name of the package to constrain
        :param current_constraint: Current constraint if any
        """
        super().__init__()
        self.package_name = package_name
        self.current_constraint = current_constraint
        self.constraint_value = ""
        self.invalidation_trigger = ""

    def compose(self) -> ComposeResult:
        """
        Create the constraint input dialog.

        :returns: Composed widgets for the dialog
        """
        # Build the widgets list
        widgets = [
            Label(f"Add constraint for: {self.package_name}", id="constraint-title")
        ]

        if self.current_constraint:
            widgets.append(Label(f"Current constraint: {self.current_constraint}", id="current-constraint"))

        widgets.extend([
            Label("Enter constraint (e.g., >=1.0.0, <2.0.0, ==1.5.0, >1.0, ~=2.1):", id="constraint-help"),
            Input(placeholder=">1.0", id="constraint-input"),
            Label("Optional invalidation trigger (e.g., requests>2.0):", id="invalidation-help"),
            Input(placeholder="requests>2.0 (optional)", id="invalidation-input"),
            Horizontal(
                Button(Text("Add Constraint", style="bold white"), id="add-constraint-btn", variant="success"),
                Button(Text("Cancel", style="bold white"), id="cancel-constraint-btn", variant="primary"),
                id="constraint-buttons"
            )
        ])  # type: ignore

        with Vertical(id="constraint-dialog"):
            for w in widgets:
                yield w

    def on_mount(self) -> None:
        """Focus the input when screen mounts."""
        self.query_one("#constraint-input", Input).focus()

    def _validate_invalidation_trigger(self, trigger: str) -> tuple[bool, str]:
        """
        Validate invalidation trigger to ensure it only uses '>' operator and package exists.

        :param trigger: Invalidation trigger string to validate
        :returns: Tuple of (is_valid, error_message)
        """
        if not trigger.strip():
            return True, ""  # Empty trigger is valid (optional)

        from ..package_constraints import parse_requirement_line, validate_package_exists
        parsed = parse_requirement_line(trigger.strip())
        if not parsed:
            return False, "Invalid trigger format. Use format like 'package>1.0'"

        # Check that the package exists
        package_name = parsed['name']
        exists, error_msg = validate_package_exists(package_name)
        if not exists:
            return False, error_msg

        constraint = parsed['constraint']
        # Check that only '>' operator is used (not '>=', '<', '<=', '==', '!=', '~=')
        if not constraint.startswith('>') or constraint.startswith('>='):
            return False, "Invalidation trigger must use only '>' operator (e.g., 'package>1.0')"

        # Additional check to ensure it's exactly '>' and not '>='
        if '>=' in constraint or '<' in constraint or '==' in constraint or '!=' in constraint or '~=' in constraint:
            return False, "Invalidation trigger must use only '>' operator (e.g., 'package>1.0')"

        return True, ""

    def _handle_constraint_submission(self) -> None:
        """Handle constraint submission with validation."""
        constraint_input = self.query_one("#constraint-input", Input)
        invalidation_input = self.query_one("#invalidation-input", Input)

        self.constraint_value = constraint_input.value.strip()
        self.invalidation_trigger = invalidation_input.value.strip()

        if not self.constraint_value:
            self.app.notify("Constraint cannot be empty", severity="error")
            return

        # Validate that the constraint package exists
        from ..package_constraints import parse_requirement_line, validate_package_exists
        constraint_spec = f"{self.package_name}{self.constraint_value}"
        parsed_constraint = parse_requirement_line(constraint_spec)
        if parsed_constraint:
            constraint_package = parsed_constraint['name']
            exists, error_msg = validate_package_exists(constraint_package)
            if not exists:
                self.app.notify(f"Constraint package error: {error_msg}", severity="error")
                return

        # Validate invalidation trigger if provided
        if self.invalidation_trigger:
            is_valid, error_msg = self._validate_invalidation_trigger(self.invalidation_trigger)
            if not is_valid:
                self.app.notify(f"Invalid trigger: {error_msg}", severity="error")
                return

        # Return both constraint and trigger as a tuple
        result = (self.constraint_value, self.invalidation_trigger) if self.invalidation_trigger else self.constraint_value
        self.dismiss(result)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        if event.input.id == "constraint-input":
            self._handle_constraint_submission()
        elif event.input.id == "invalidation-input":
            self._handle_constraint_submission()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "add-constraint-btn":
            self._handle_constraint_submission()
        elif event.button.id == "cancel-constraint-btn":
            self.dismiss(None)

    def action_add_constraint(self) -> None:
        """Add constraint action (triggered by Enter key)."""
        self._handle_constraint_submission()

    def action_cancel(self) -> None:
        """Cancel action (triggered by Escape key)."""
        self.dismiss(None)

    CSS = """
    ConstraintInputScreen {
        align: center middle;
    }

    #constraint-dialog {
        width: 70;
        max-width: 90%;
        height: auto;
        min-height: 16;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: thick $background 80%;
        content-align: left top;
        dock: none;
    }

    #constraint-title {
        text-style: bold;
        text-align: center;
        padding: 0;
        margin: 0 0 1 0;
    }

    #constraint-help, #invalidation-help {
        text-style: italic;
        padding: 0;
        margin: 0 0 0 0;
        color: $text 70%;
    }

    #current-constraint {
        color: $warning;
        text-style: italic;
        padding: 0;
        margin: 0 0 1 0;
    }

    #constraint-input, #invalidation-input {
        height: 3;
        min-height: 3;
        max-height: 3;
        padding: 0 1;
        background: $surface-lighten-1;
        color: $text;
        border: solid $primary;
        content-align: left middle;
    }

    #constraint-buttons {
        padding: 0;
        margin: 1 0 0 0;
        height: 3;
        content-align: center middle;
    }

    #constraint-buttons > Button {
        width: 1fr;
        height: 3;
        margin: 0 1;
        text-align: center;
        text-style: bold;
        color: white;
    }

    /* Ensure button text is visible */
    ConstraintInputScreen Button > .label,
    ConstraintInputScreen Button .button--label {
        color: white !important;
        text-style: bold;
    }

    /* Consistent focus highlighting for tab navigation */
    ConstraintInputScreen Button:focus {
        text-style: bold !important;
        color: white !important;
        border: thick $accent !important;
    }
    """


class HelpScreen(ModalScreen):
    """Modal screen showing keyboard shortcuts and help information."""

    BINDINGS = [
        ("escape,h", "dismiss", "Close Help"),
    ]

    def compose(self) -> ComposeResult:
        """Create the help dialog with comprehensive information."""
        with Vertical(id="help-dialog"):
            yield Label("pipu - Package Management Help", id="help-title")

            with ScrollableContainer(id="help-content"):
                # Create help table
                help_table = DataTable(id="help-table")
                help_table.add_column("Key", width=12)
                help_table.add_column("Action", width=20)
                help_table.add_column("Description", width=50)

                # Add keyboard shortcuts
                shortcuts = [
                    ("↑/↓", "Navigate", "Move cursor up/down through package list"),
                    ("Space", "Toggle Selection", "Select/deselect package for update"),
                    ("U", "Update Selected", "Start updating all selected packages"),
                    ("C", "Add Constraint", "Add version constraint to current package"),
                    ("D", "Delete Constraint", "Delete constraint from current package"),
                    ("R", "Remove All Constraints", "Remove all constraints from configuration"),
                    ("X", "Uninstall", "Uninstall the currently selected package"),
                    ("F", "Filter Outdated", "Show only packages with available updates"),
                    ("S", "Show All", "Show all installed packages"),
                    ("H", "Help", "Show this help dialog"),
                    ("Q/Esc", "Quit", "Exit the application"),
                ]

                for key, action, description in shortcuts:
                    help_table.add_row(key, action, description)

                yield help_table

                yield Label("Features Overview:", id="features-title")
                yield Static(
                    "• Constraints: Add version constraints (e.g., >=1.0.0, <2.0.0) to prevent unwanted updates\n"
                    "• Auto-Discovered Constraints: Constraints are automatically discovered from installed packages on every run\n"
                    "• Invalidation Triggers: Constraints can be automatically removed when trigger packages are updated\n"
                    "• Real-time Updates: Package information updates as checks complete\n"
                    "• Smart Selection: Packages are auto-selected only if they satisfy existing constraints\n"
                    "• Filter Modes: View all packages or filter to show only those with updates available",
                    id="features-text"
                )

                yield Label("Auto-Discovered Constraints - How They Work:", id="auto-constraints-title")
                yield Static(
                    "Auto-discovered constraints are automatically generated each time pipu runs by analyzing\n"
                    "your installed packages and their dependencies. They are transient and never written to config.\n\n"
                    "How Auto-Discovery Works:\n"
                    "1. Scans all installed packages and their version requirements on every pipu execution\n"
                    "2. Identifies packages that depend on specific versions of other packages\n"
                    "3. Creates temporary constraints to prevent breaking these dependencies\n"
                    "4. Merges with your manual constraints (manual constraints always take precedence)\n\n"
                    "Example: If package 'requests' requires 'urllib3>=1.21.1,<3', pipu will automatically\n"
                    "apply a constraint for urllib3 to prevent updates that could break requests.\n\n"
                    "Benefits:\n"
                    "• Prevents dependency conflicts during updates automatically\n"
                    "• Maintains package compatibility without manual intervention\n"
                    "• Reduces the risk of broken installations\n"
                    "• Always reflects current package state (no stale constraints)",
                    id="auto-constraints-text"
                )

                yield Label("Constraint Invalidation Triggers:", id="triggers-title")
                yield Static(
                    "Invalidation triggers automatically remove constraints when specific conditions are met.\n\n"
                    "How Triggers Work:\n"
                    "1. Each auto constraint will have at least one package that 'triggers' its invalidation\n"
                    "2. When a trigger package is updated, its related constraints are removed\n"
                    "3. This prevents outdated constraints from blocking future updates\n\n"
                    "Example Workflow:\n"
                    "• Auto constraint created: 'urllib3<2.0.0' (triggered by requests v2.28.0)\n"
                    "• Later, requests is updated to v2.31.0 (which supports urllib3 v2.x)\n"
                    "• The urllib3 constraint is automatically removed\n"
                    "• urllib3 can now be updated to newer versions\n\n"
                    "Why This Matters:\n"
                    "• Constraints become outdated as dependencies evolve\n"
                    "• Manual constraint management is error-prone and time-consuming\n"
                    "• Triggers ensure constraints stay relevant and don't block legitimate updates\n"
                    "• Maintains the balance between stability and staying current",
                    id="triggers-text"
                )

                yield Label("Tips for Best Results:", id="tips-title")
                yield Static(
                    "• Use 'F' to filter and focus on packages that actually need updates\n"
                    "• Auto-discovered constraints protect dependencies automatically (no manual action needed)\n"
                    "• Review constraint colors: green = can update, red = blocked by constraint\n"
                    "• Use 'C' to add custom manual constraints for packages you want to pin\n"
                    "• Check 'Constraint Invalid When' column to understand when constraints will be removed",
                    id="tips-text"
                )

            with Horizontal(id="help-buttons"):
                yield Button(Text("Close", style="bold white"), id="close-help-btn", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "close-help-btn":
            self.dismiss()

    async def action_dismiss(self, result: Any = None) -> None:
        """Close the help dialog."""
        self.dismiss(result)

    CSS = """
    HelpScreen {
        align: center middle;
        layer: overlay;
    }

    #help-dialog {
        padding: 2;
        width: 90;
        max-width: 95%;
        height: auto;
        min-height: 30;
        max-height: 90%;
        border: thick $primary;
        background: $surface;
        content-align: left top;
        dock: none;
    }

    #help-title {
        text-style: bold;
        text-align: center;
        padding: 0 0 1 0;
        color: $text;
    }

    #help-content {
        padding: 0 1 1 1;
        background: transparent;
    }

    #help-table {
        margin: 0 0 1 0;
        background: transparent;
    }

    #features-title, #auto-constraints-title, #triggers-title, #tips-title {
        text-style: bold;
        padding: 1 0 0 0;
        color: $accent;
    }

    #features-text, #auto-constraints-text, #triggers-text, #tips-text {
        padding: 0 0 1 0;
        color: $text;
    }

    #help-buttons {
        padding: 1 0 0 0;
        height: 5;
        align: center middle;
    }

    #help-buttons Button {
        width: 20;
        margin: 0 2;
    }

    /* Consistent button styling */
    #close-help-btn {
        background: $primary;
        border: tall $primary;
        color: white;
        text-style: bold;
    }

    #close-help-btn:focus {
        text-style: bold !important;
        color: white !important;
        border: thick $accent !important;
    }
    """


class DeleteConstraintConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm constraint deletion."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, package_name: str, constraint: str):
        super().__init__()
        self.package_name = package_name
        self.constraint = constraint

    def compose(self) -> ComposeResult:
        # Use rich.Text with inline styling + Horizontal container to escape grid row styling
        confirm = Button(Text("Yes, Delete", style="bold white"), id="confirm", variant="error")
        cancel = Button(Text("Cancel", style="bold white"), id="cancel", variant="primary")

        yield Grid(
            Label(f"Delete constraint '{self.constraint}' for '{self.package_name}'?", id="question"),
            Label("This will remove the constraint and any invalidation triggers.", id="warning"),
            Horizontal(
                confirm,
                cancel,
                id="actions",
            ),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Cancel constraint deletion (triggered by Escape key)."""
        self.dismiss(False)

    CSS = """
    DeleteConstraintConfirmScreen {
        align: center middle;
    }

    DeleteConstraintConfirmScreen Button {
        width: 100%;
        height: 3;
        margin: 1 0;
        text-align: center;
    }

    /* Let variants handle label styling
    DeleteConstraintConfirmScreen Button > .label,
    DeleteConstraintConfirmScreen Button .button--label {
        color: white !important;
        text-style: bold;
        opacity: 1.0 !important;
    }
    */

    /* Explicit button styling for test validation */
    #actions > #confirm {
        background: $error;
        border: tall $error;
        color: white;
    }

    #actions > #cancel {
        background: $primary;
        border: tall $primary;
        color: white;
    }

    #actions {
        column-span: 2;               /* the row spans both columns */
        height: 4;
        content-align: center middle;
        padding: 0 1;
    }

    #actions > Button {
        width: 1fr;
        height: 3;
        margin: 0 1;
    }

    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 1fr 4;
        padding: 0 1;
        width: 70;
        height: 13;
        border: thick $background 80%;
        background: $surface;
    }

    #question {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        text-style: bold;
    }

    #warning {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        color: $warning;
        text-style: italic;
    }

    /* Safety net for text color and focus consistency */
    DeleteConstraintConfirmScreen Button,
    DeleteConstraintConfirmScreen Button > .label,
    DeleteConstraintConfirmScreen Button .button--label {
        color: white !important;
        text-style: bold;
    }

    /* Consistent focus highlighting for tab navigation */
    DeleteConstraintConfirmScreen Button:focus {
        text-style: bold !important;
        color: white !important;
        border: thick $accent !important;
    }

    """


class RemoveAllConstraintsConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm removal of all constraints."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, constraint_count: int):
        super().__init__()
        self.constraint_count = constraint_count

    def compose(self) -> ComposeResult:
        # Use rich.Text with inline styling + Horizontal container for consistent modal styling
        confirm = Button(Text("Yes, Remove All", style="bold white"), id="confirm", variant="error")
        cancel = Button(Text("Cancel", style="bold white"), id="cancel", variant="primary")

        with Vertical(id="remove-all-dialog"):
            yield Label(f"Remove all {self.constraint_count} constraints?", id="question")
            yield Label("This will remove ALL constraints and invalidation triggers from your pip configuration.", id="warning")
            yield Label("This action cannot be undone!", id="final-warning")
            with Horizontal(id="actions"):
                yield confirm
                yield cancel

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Cancel removal of all constraints (triggered by Escape key)."""
        self.dismiss(False)

    CSS = """
    RemoveAllConstraintsConfirmScreen {
        align: center middle;
        layer: overlay;
    }

    #remove-all-dialog {
        padding: 2;
        width: 60;
        max-width: 90%;
        height: auto;
        min-height: 16;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        content-align: left top;
        dock: none;
    }

    #question {
        text-style: bold;
        text-align: center;
        padding: 0 0 1 0;
        color: $text;
    }

    #warning {
        text-align: center;
        padding: 0 0 1 0;
        color: $warning;
        text-style: italic;
    }

    #final-warning {
        text-align: center;
        padding: 0 0 1 0;
        color: $error;
        text-style: bold;
    }

    #actions {
        padding: 1 0 0 0;
        height: 5;
        align: center middle;
    }

    #actions Button {
        width: 20;
        margin: 0 2;
    }

    /* Explicit button styling for consistency */
    #actions > #confirm {
        background: $error;
        border: tall $error;
        color: white;
        text-style: bold;
    }

    #actions > #cancel {
        background: $primary;
        border: tall $primary;
        color: white;
        text-style: bold;
    }

    /* Safety net for text color and focus consistency */
    RemoveAllConstraintsConfirmScreen Button,
    RemoveAllConstraintsConfirmScreen Button > .label,
    RemoveAllConstraintsConfirmScreen Button .button--label {
        color: white !important;
        text-style: bold;
    }

    /* Consistent focus highlighting for tab navigation */
    RemoveAllConstraintsConfirmScreen Button:focus {
        text-style: bold !important;
        color: white !important;
        border: thick $accent !important;
    }

    """


class UninstallConfirmScreen(BaseConfirmationScreen):
    """Modal screen to confirm package uninstall."""

    def __init__(self, package_name: str):
        message = f"Are you sure you want to uninstall '{package_name}'?"
        super().__init__(
            message=message,
            confirm_text="Yes, Uninstall",
            cancel_text="Cancel",
            confirm_variant="error",
            cancel_variant="primary"
        )


class UpdateConfirmScreen(ModalScreen[bool]):
    """Modal screen to confirm package updates."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, selected_packages: List[Dict[str, Any]]):
        super().__init__()
        self.selected_packages = selected_packages

    def compose(self) -> ComposeResult:
        package_count = len(self.selected_packages)

        if package_count <= 3:
            package_list = ", ".join([pkg["name"] for pkg in self.selected_packages])
        else:
            package_list = ", ".join([pkg["name"] for pkg in self.selected_packages[:3]])
            package_list += f" and {package_count - 3} more"

        # Create summary of what will be updated
        editable_count = sum(1 for pkg in self.selected_packages if pkg.get("editable", False))
        constrained_count = sum(1 for pkg in self.selected_packages if pkg.get("constraint"))

        summary_parts = [f"{package_count} packages"]
        if editable_count > 0:
            summary_parts.append(f"{editable_count} editable")
        if constrained_count > 0:
            summary_parts.append(f"{constrained_count} with constraints")

        summary = f"Update {', '.join(summary_parts)}?"

        confirm = Button(Text("Yes, Update", style="bold white"), id="confirm", variant="success")
        cancel = Button(Text("Cancel", style="bold white"), id="cancel", variant="primary")

        yield Grid(
            Label(f"{summary}\n\nPackages: {package_list}", id="question"),
            Horizontal(
                confirm,
                cancel,
                id="actions",
            ),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Cancel package update (triggered by Escape key)."""
        self.dismiss(False)

    CSS = """
    UpdateConfirmScreen {
        align: center middle;
    }

    UpdateConfirmScreen Button {
        height: 3;
        margin: 0 1;
        text-align: center;
    }

    #actions {
        column-span: 2;               /* the row spans both columns */
        height: 4;
        content-align: center middle;
        padding: 0 1;
    }

    #actions > Button {
        width: 1fr;
        height: 3;
        margin: 0 1;
    }

    /* Explicit button styling for confirmation */
    #actions > #confirm {
        background: $success;
        border: tall $success;
        color: white;
    }

    #actions > #cancel {
        background: $primary;
        border: tall $primary;
        color: white;
    }

    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 4;
        padding: 0 1;
        width: 60;
        height: 15;  /* Slightly taller for update details */
        border: thick $background 80%;
        background: $surface;
    }

    #question {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }

    /* Safety net for text color and focus consistency */
    UpdateConfirmScreen Button,
    UpdateConfirmScreen Button > .label,
    UpdateConfirmScreen Button .button--label {
        color: white !important;
        text-style: bold;
    }

    /* Consistent focus highlighting for tab navigation */
    UpdateConfirmScreen Button:focus {
        text-style: bold !important;
        color: white !important;
        border: thick $accent !important;
    }

    """


class PackageUpdateScreen(ModalScreen[None]):
    """Full-screen modal for showing package update progress and handling cleanup."""

    BINDINGS = [
        ("escape", "handle_escape", "Cancel/Exit"),
        ("enter", "handle_enter", "Exit (when complete)"),
    ]

    def __init__(self, selected_packages: List[Dict[str, Any]]):
        super().__init__()
        self.selected_packages = selected_packages
        self.update_complete = False
        self.successful_updates: List[str] = []
        self.failed_updates: List[str] = []
        self.cancel_event: Optional[threading.Event] = None
        self._log_content = ""  # Track log content internally

    def compose(self) -> ComposeResult:
        package_count = len(self.selected_packages)

        yield Grid(
            Static(f"Updating {package_count} Packages", id="title"),
            Static("Preparing updates...", id="status"),
            ScrollableContainer(
                Static("", id="progress-log"),
                id="log-container"
            ),
            Static("Press Escape to cancel updates", id="footer"),
            id="update-dialog",
        )

    def on_mount(self) -> None:
        """Start the update process when the screen is mounted."""
        logger.info(f"PackageUpdateScreen mounted with {len(self.selected_packages)} packages")
        self._log_message(f"📋 Preparing to update {len(self.selected_packages)} packages...")
        # Use a lambda to avoid the call_later passing extra arguments
        self.call_later(lambda: self._start_update_process())

    def _start_update_process(self) -> None:
        """Start the package update process in a worker thread."""
        logger.info("Starting update process...")
        self._update_status("Starting package updates...")
        self._log_message("🚀 Beginning package update process...")

        # Create a cancellation event
        self.cancel_event = threading.Event()

        def run_updates():
            """Run package updates in a worker thread."""
            # Ensure cancel_event is set (should always be true when called from _start_update_process)
            if self.cancel_event is None:
                raise RuntimeError("Update process started without cancel_event being initialized")

            try:
                import subprocess
                import sys
                import tempfile
                import os
                from packaging.utils import canonicalize_name

                logger.info(f"Starting batch update for {len(self.selected_packages)} packages")
                total_packages = len(self.selected_packages)

                # Get canonical names of packages being updated
                from ..package_constraints import read_constraints
                all_constraints = read_constraints()
                packages_being_updated = {canonicalize_name(pkg["name"]) for pkg in self.selected_packages}

                # Filter out constraints for packages being updated to avoid conflicts
                filtered_constraints = {
                    pkg: constraint
                    for pkg, constraint in all_constraints.items()
                    if pkg not in packages_being_updated
                }

                # Create a temporary constraints file if there are any constraints to apply
                constraint_file = None
                constraint_file_path = None
                try:
                    if filtered_constraints:
                        constraint_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
                        constraint_file_path = constraint_file.name
                        for pkg, constraint in filtered_constraints.items():
                            constraint_file.write(f"{pkg}{constraint}\n")
                        constraint_file.close()
                        self.app.call_from_thread(self._log_message, f"[dim]Using filtered constraints (excluding {len(packages_being_updated)} package(s) being updated)[/dim]")

                    # Build list of package names to update
                    # Use --upgrade instead of pinning versions to avoid dependency conflicts
                    # when updating interdependent packages (e.g., pydantic and pydantic-core)
                    package_names = []
                    for pkg in self.selected_packages:
                        package_names.append(pkg["name"])

                    self.app.call_from_thread(self._update_status, f"Updating {total_packages} packages...")
                    self.app.call_from_thread(self._log_message, f"{'='*70}")
                    self.app.call_from_thread(self._log_message, f"📦 Updating {total_packages} packages: {', '.join(package_names[:5])}")
                    if len(package_names) > 5:
                        self.app.call_from_thread(self._log_message, f"   ... and {len(package_names) - 5} more")
                    self.app.call_from_thread(self._log_message, f"{'='*70}\n")

                    # Prepare pip command to install all packages with --upgrade
                    # This allows pip's dependency resolver to find compatible versions
                    # for interdependent packages (e.g., pydantic requires specific pydantic-core)
                    pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + package_names

                    # Set up environment with constraint file if available
                    env = os.environ.copy()
                    if constraint_file_path:
                        env['PIP_CONSTRAINT'] = constraint_file_path

                    # Run pip and capture output with proper cleanup
                    from ..utils import ManagedProcess
                    return_code = None

                    try:
                        with ManagedProcess(
                            pip_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            universal_newlines=True,
                            env=env
                        ) as process:
                            # Stream output line by line
                            if process.stdout is None:
                                raise RuntimeError("Failed to capture subprocess output")
                            for line in process.stdout:
                                if self.cancel_event.is_set():
                                    self.app.call_from_thread(self._log_message, "\n🛑 Update cancelled by user")
                                    break
                                # Display each line of pip output
                                self.app.call_from_thread(self._log_message, line.rstrip())

                            # Wait for process to complete
                            return_code = process.wait()
                    except Exception as e:
                        logger.error(f"Error during package update: {e}")
                        self.app.call_from_thread(self._log_message, f"\n❌ Error: {e}")
                        return_code = 1

                    if return_code == 0:
                        # All packages updated successfully
                        self.successful_updates.extend(package_names)
                        self.app.call_from_thread(self._log_message, f"\n{'='*70}")
                        self.app.call_from_thread(self._log_message, f"✅ Successfully updated all {total_packages} packages!")
                        self.app.call_from_thread(self._log_message, f"{'='*70}")
                    else:
                        # Some packages failed - pip will have shown which ones in output
                        self.failed_updates.extend(package_names)
                        self.app.call_from_thread(self._log_message, f"\n{'='*70}")
                        self.app.call_from_thread(self._log_message, "❌ Update completed with errors (see above)")
                        self.app.call_from_thread(self._log_message, f"{'='*70}")

                    # Show final results and cleanup
                    logger.info("Update loop completed, calling _update_complete")
                    self.app.call_from_thread(self._update_complete)

                finally:
                    # Clean up temporary constraint file
                    if constraint_file_path and os.path.exists(constraint_file_path):
                        try:
                            os.unlink(constraint_file_path)
                        except Exception:
                            pass  # Best effort cleanup

            except Exception as e:
                logger.error(f"Error in update loop: {e}", exc_info=True)
                self.app.call_from_thread(self._update_error, str(e))

        # Run the updates in a worker thread
        logger.info("Starting worker thread for updates...")
        self.run_worker(run_updates, thread=True, exclusive=False, name="package_updates")
        logger.info("Worker thread started")

    def _update_status(self, message: str) -> None:
        """Update the status message."""
        try:
            status_widget = self.query_one("#status", Static)
            status_widget.update(message)
        except Exception:
            pass

    def _log_message(self, message: str) -> None:
        """Add a message to the progress log."""
        try:
            log_widget = self.query_one("#progress-log", Static)
            # Get current content - use render_str() or access the internal content
            try:
                current_content = str(log_widget.render())
            except Exception:
                # Fallback: keep track of content ourselves
                if not hasattr(self, '_log_content'):
                    self._log_content = ""
                current_content = self._log_content

            # Append new message
            if current_content:
                new_content = f"{current_content}\n{message}"
            else:
                new_content = message

            # Update the widget
            log_widget.update(new_content)

            # Save content for next time
            self._log_content = new_content

            # Auto-scroll to bottom
            log_container = self.query_one("#log-container", ScrollableContainer)
            log_container.scroll_end()
        except Exception as e:
            logger.error(f"Error updating log: {e}", exc_info=True)

    def _update_complete(self) -> None:
        """Handle completion of the update process."""
        self.update_complete = True

        # Show final results
        success_count = len(self.successful_updates)
        failure_count = len(self.failed_updates)

        if success_count > 0 and failure_count > 0:
            final_message = f"✅ Updated {success_count} packages, ❌ {failure_count} failed"
        elif success_count > 0:
            final_message = f"✅ Successfully updated all {success_count} packages!"
        elif failure_count > 0:
            final_message = f"❌ Failed to update {failure_count} packages"
        else:
            final_message = "⚠️ No packages were updated"

        self._update_status(final_message)
        self._log_message(f"\n{final_message}")

        # If we had successful updates, clean up invalidation triggers
        if self.successful_updates:
            self._log_message("\n🧹 Cleaning up invalidation triggers...")
            self._cleanup_invalid_triggers()

        self._log_message("\n🎉 Update process complete! Press Enter or Escape to exit.")

        # Update footer to show completion
        try:
            footer_widget = self.query_one("#footer", Static)
            footer_widget.update("Update complete! Press Enter or Escape to exit the application.")
        except Exception:
            pass

    def _update_error(self, error_message: str) -> None:
        """Handle update process error."""
        self.update_complete = True
        self._update_status(f"❌ Update process failed: {error_message}")
        self._log_message(f"❌ Fatal error: {error_message}")
        self._log_message("\nPress Escape to exit.")

        # Update footer
        try:
            footer_widget = self.query_one("#footer", Static)
            footer_widget.update("Error occurred! Press Escape to exit the application.")
        except Exception:
            pass

    def _cleanup_invalid_triggers(self) -> None:
        """Clean up invalidation triggers that are no longer valid after updates."""
        try:
            from ..package_constraints import cleanup_invalidated_constraints

            removed_constraints, trigger_details, summary_message = cleanup_invalidated_constraints()

            if summary_message:
                self._log_message(f"🧹 {summary_message}")

                # Show details of what was cleaned up
                if trigger_details:
                    for constrained_package, satisfied_triggers in trigger_details.items():
                        triggers_str = ", ".join(satisfied_triggers)
                        self._log_message(f"   • Removed constraint for {constrained_package} (triggers: {triggers_str})")
            else:
                self._log_message("🧹 No constraint cleanup needed")

        except Exception as e:
            self._log_message(f"⚠️ Error during constraint cleanup: {e}")

    def action_handle_escape(self) -> None:
        """Handle escape key press - cancel updates or exit."""
        if self.update_complete:
            # Exit the entire application when updates are complete
            self.app.exit()
        else:
            # Cancel ongoing updates
            if self.cancel_event:
                self._log_message("\n🛑 Cancelling updates... please wait for current package to finish")
                self._update_status("Cancelling updates...")
                self.cancel_event.set()
                # Update footer
                try:
                    footer_widget = self.query_one("#footer", Static)
                    footer_widget.update("Cancelling... Press Escape again after completion to exit.")
                except Exception:
                    pass

    def action_handle_enter(self) -> None:
        """Handle enter key press - exit only when update is complete."""
        if self.update_complete:
            # Only exit when updates are done
            self.app.exit()
        else:
            # Ignore Enter while updates are in progress
            pass

    CSS = """
    PackageUpdateScreen {
        align: center middle;
        layer: overlay;
    }

    #update-dialog {
        grid-size: 1;
        grid-rows: 3 2 1fr 2;
        padding: 1;
        width: 95%;
        height: 90%;
        border: thick $primary;
        background: $surface;
    }

    #title {
        text-align: center;
        text-style: bold;
        color: $primary;
        height: 3;
        content-align: center middle;
    }

    #status {
        text-align: center;
        height: 2;
        content-align: center middle;
        color: $text;
    }

    #log-container {
        border: solid $primary;
        height: 1fr;
        margin: 0;
        padding: 1;
        background: $background;
        overflow-y: auto;
    }

    #progress-log {
        color: $text;
        background: $background;
        height: auto;
        width: 100%;
    }

    #footer {
        text-align: center;
        height: 2;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    """


class NetworkErrorScreen(ModalScreen[None]):
    """Modal screen to display network error and exit."""

    BINDINGS = [
        ("escape", "exit_app", "Exit"),
        ("enter", "exit_app", "Exit"),
    ]

    def __init__(self, error_message: str):
        super().__init__()
        self.error_message = error_message

    def compose(self) -> ComposeResult:
        ok_button = Button(Text("OK", style="bold white"), id="ok", variant="error")

        with Vertical(id="network-error-dialog"):
            yield Label("Network Error", id="error-title")
            yield Label(self.error_message, id="error-message")
            with Horizontal(id="actions"):
                yield ok_button

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.app.exit()

    def action_exit_app(self) -> None:
        """Exit the application (triggered by Escape or Enter key)."""
        self.app.exit()

    CSS = """
    NetworkErrorScreen {
        align: center middle;
        layer: overlay;
    }

    #network-error-dialog {
        padding: 2;
        width: 70;
        max-width: 90%;
        height: auto;
        min-height: 12;
        max-height: 80%;
        border: thick $error;
        background: $surface;
        content-align: left top;
        dock: none;
    }

    #error-title {
        text-style: bold;
        text-align: center;
        padding: 0 0 1 0;
        color: $error;
    }

    #error-message {
        text-align: left;
        padding: 0 0 1 0;
        color: $text;
    }

    #actions {
        padding: 1 0 0 0;
        height: 5;
        align: center middle;
    }

    #actions Button {
        margin: 0 1;
        min-width: 12;
    }
    """