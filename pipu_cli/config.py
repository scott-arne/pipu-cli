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
# Logging Configuration
# ============================================================================

# Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL_STR = os.environ.get('PIPU_LOG_LEVEL', 'WARNING').upper()

# Convert string to logging level
try:
    LOG_LEVEL = getattr(logging, LOG_LEVEL_STR)
except AttributeError:
    LOG_LEVEL = logging.WARNING

