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

# UI timeouts and limits
FORCE_EXIT_TIMEOUT = 0.5  # seconds
UNINSTALL_TIMEOUT = 30    # seconds
NETWORK_TIMEOUT_TEST = 1  # seconds - for fast test execution