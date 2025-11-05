"""
Configuration constants and settings for pipu.

This module centralizes all configuration values, magic numbers, and
environment-based settings to improve maintainability.
"""

import os
import logging

# ============================================================================
# Network Configuration
# ============================================================================

# Default timeout for network operations (seconds)
DEFAULT_NETWORK_TIMEOUT = int(os.environ.get('PIPU_TIMEOUT', '10'))

# Number of retries for failed network operations
DEFAULT_NETWORK_RETRIES = int(os.environ.get('PIPU_RETRIES', '0'))

# Maximum consecutive network errors before failing
MAX_CONSECUTIVE_NETWORK_ERRORS = int(os.environ.get('PIPU_MAX_NETWORK_ERRORS', '1'))

# Brief delay between retries (seconds)
RETRY_DELAY = float(os.environ.get('PIPU_RETRY_DELAY', '0.5'))

# ============================================================================
# Cache Configuration
# ============================================================================

# Time-to-live for editable packages cache (seconds)
EDITABLE_PACKAGES_CACHE_TTL = float(os.environ.get('PIPU_CACHE_TTL', '60.0'))

# ============================================================================
# Subprocess Configuration
# ============================================================================

# Timeout for subprocess operations (seconds)
SUBPROCESS_TIMEOUT = int(os.environ.get('PIPU_SUBPROCESS_TIMEOUT', '30'))

# Timeout for package uninstall operations (seconds)
UNINSTALL_TIMEOUT = int(os.environ.get('PIPU_UNINSTALL_TIMEOUT', '120'))

# Timeout for forced process termination (seconds)
FORCE_KILL_TIMEOUT = float(os.environ.get('PIPU_FORCE_KILL_TIMEOUT', '5.0'))

# ============================================================================
# TUI Configuration
# ============================================================================

# Timeout for graceful exit (seconds)
FORCE_EXIT_TIMEOUT = float(os.environ.get('PIPU_EXIT_TIMEOUT', '3.0'))

# Scroll buffer size for log output
LOG_SCROLL_BUFFER_LINES = int(os.environ.get('PIPU_LOG_BUFFER', '1000'))

# Delay for table refresh operations (seconds)
TABLE_REFRESH_DELAY = float(os.environ.get('PIPU_TABLE_REFRESH', '0.01'))

# ============================================================================
# Logging Configuration
# ============================================================================

# Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL_STR = os.environ.get('PIPU_LOG_LEVEL', 'WARNING').upper()

# Convert string to logging level
try:
    LOG_LEVEL = getattr(logging, LOG_LEVEL_STR)
except AttributeError:
    LOG_LEVEL = logging.WARNING

# ============================================================================
# Testing Configuration
# ============================================================================

# Skip package validation in tests (set by test fixtures)
SKIP_PACKAGE_VALIDATION = os.environ.get('PIPU_SKIP_PKG_VALIDATION', '').lower() in ('1', 'true', 'yes')

# ============================================================================
# File Paths
# ============================================================================

# Default constraints file name
DEFAULT_CONSTRAINTS_FILE = 'constraints.txt'

# Default ignores file name
DEFAULT_IGNORES_FILE = 'ignores.txt'

# ============================================================================
# Version Display
# ============================================================================

# Maximum packages to show in summary before truncating
MAX_PACKAGES_DISPLAY = 5
