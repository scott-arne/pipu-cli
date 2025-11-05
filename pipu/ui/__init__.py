# UI Module for pipu package management
"""
Textual-based TUI interfaces for pipu package management.

This module provides the main TUI applications and supporting components
for interactive package management, selection, and constraint configuration.
"""

from .apps import MainTUIApp, PackageSelectionApp, main_tui_app, interactive_package_selection
from .modal_dialogs import (
    ConstraintInputScreen, HelpScreen,
    DeleteConstraintConfirmScreen, RemoveAllConstraintsConfirmScreen,
    UninstallConfirmScreen, NetworkErrorScreen
)
from .table_widgets import PackageSelectionTable
from .constants import (
    COLUMN_SELECTION, COLUMN_PACKAGE, COLUMN_CURRENT, COLUMN_LATEST,
    COLUMN_TYPE, COLUMN_CONSTRAINT, COLUMN_INVALID_WHEN,
    FORCE_EXIT_TIMEOUT, UNINSTALL_TIMEOUT
)

# Main entry point functions
__all__ = [
    # Main applications
    'MainTUIApp',
    'PackageSelectionApp',
    'main_tui_app',
    'interactive_package_selection',

    # Modal dialogs
    'ConstraintInputScreen',
    'HelpScreen',
    'DeleteConstraintConfirmScreen',
    'RemoveAllConstraintsConfirmScreen',
    'UninstallConfirmScreen',
    'NetworkErrorScreen',

    # Table widgets
    'PackageSelectionTable',

    # Constants
    'COLUMN_SELECTION',
    'COLUMN_PACKAGE',
    'COLUMN_CURRENT',
    'COLUMN_LATEST',
    'COLUMN_TYPE',
    'COLUMN_CONSTRAINT',
    'COLUMN_INVALID_WHEN',
    'FORCE_EXIT_TIMEOUT',
    'UNINSTALL_TIMEOUT'
]