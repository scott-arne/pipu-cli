"""
Constants and configuration for the TUI interface.

Contains column definitions, timeouts, and other UI constants.
"""

# Table column indices
COLUMN_SELECTION = 0
COLUMN_PACKAGE = 1
COLUMN_CURRENT = 2
COLUMN_LATEST = 3
COLUMN_TYPE = 4
COLUMN_CONSTRAINT = 5
COLUMN_INVALID_WHEN = 6

# Table column definitions for consistent display across UI
# These can be used to ensure all tables have the same structure
TABLE_COLUMNS = {
    "package": {"title": "Package", "width": 20},
    "current": {"title": "Current", "width": 12},
    "latest": {"title": "Latest", "width": 12},
    "type": {"title": "Type", "width": 8},
    "constraint": {"title": "Constraint", "width": 20},
    "constraint_invalid_when": {"title": "Constraint Invalid When", "width": 30},
}

# Config option name
CONFIG_CONSTRAINT_INVALID_WHEN = 'constraint_invalid_when'

# UI timeouts and limits
FORCE_EXIT_TIMEOUT = 0.5  # seconds
UNINSTALL_TIMEOUT = 30    # seconds
NETWORK_TIMEOUT_TEST = 1  # seconds - for fast test execution