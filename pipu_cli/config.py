"""
Configuration constants and settings for pipu.

This module centralizes all configuration values, magic numbers, and
environment-based settings to improve maintainability.
"""

import os
import logging
from pathlib import Path

# ============================================================================
# Network Configuration
# ============================================================================

# Default timeout for network operations (seconds)
DEFAULT_NETWORK_TIMEOUT = int(os.environ.get('PIPU_TIMEOUT', '10'))

# ============================================================================
# Cache Configuration
# ============================================================================

# Default cache TTL in seconds (1 hour)
DEFAULT_CACHE_TTL = int(os.environ.get('PIPU_CACHE_TTL', '3600'))

# Whether caching is enabled by default
DEFAULT_CACHE_ENABLED = os.environ.get('PIPU_CACHE_ENABLED', 'true').lower() in ('true', '1', 'yes')

# Base directory for cache storage
CACHE_BASE_DIR = Path(os.environ.get('PIPU_CACHE_DIR', str(Path.home() / ".pipu" / "cache")))

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

