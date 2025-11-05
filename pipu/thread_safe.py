"""
Thread-safe data structures and utilities for pipu.

Provides thread-safe implementations of caches and shared state management
to prevent race conditions in concurrent operations.
"""

import threading
import time
import logging
from typing import Dict, List, Any, Optional, TypeVar, Generic, Callable

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ThreadSafeCache(Generic[T]):
    """
    Thread-safe cache with TTL support.

    This cache is safe for concurrent access from multiple threads.
    """

    def __init__(self, ttl: float = 60.0):
        """
        Initialize a thread-safe cache.

        :param ttl: Time-to-live for cache entries in seconds
        """
        self._lock = threading.RLock()  # Reentrant lock
        self._cache: Optional[T] = None
        self._cache_time: float = 0.0
        self._ttl = ttl

    def get(self, factory: Callable[[], T]) -> T:
        """
        Get cached value or create it using the factory function.

        :param factory: Function to create value if cache is expired
        :returns: Cached or freshly created value
        """
        with self._lock:
            current_time = time.time()

            # Check if cache is valid
            if self._cache is not None and (current_time - self._cache_time) < self._ttl:
                logger.debug("Cache hit")
                # Type checker: we've verified self._cache is not None above
                return self._cache  # type: ignore[return-value]

            # Cache miss or expired
            logger.debug("Cache miss - creating new value")
            try:
                new_value: T = factory()
                self._cache = new_value
                self._cache_time = time.time()
                return new_value
            except Exception as e:
                logger.error(f"Factory function failed: {e}")
                # Return stale cache if available, otherwise raise
                if self._cache is not None:
                    logger.warning("Returning stale cache due to factory failure")
                    # Type checker: we've verified self._cache is not None above
                    return self._cache  # type: ignore[return-value]
                raise

    def invalidate(self):
        """Invalidate the cache."""
        with self._lock:
            self._cache = None
            self._cache_time = 0.0
            logger.debug("Cache invalidated")

    def is_valid(self) -> bool:
        """Check if cache has valid data."""
        with self._lock:
            if self._cache is None:
                return False
            current_time = time.time()
            return (current_time - self._cache_time) < self._ttl


class ThreadSafeList(Generic[T]):
    """
    Thread-safe list wrapper.

    Provides synchronized access to a list for concurrent operations.
    """

    def __init__(self, initial: Optional[List[T]] = None):
        """
        Initialize a thread-safe list.

        :param initial: Initial list contents
        """
        self._lock = threading.RLock()
        self._items: List[T] = initial.copy() if initial else []

    def get_all(self) -> List[T]:
        """Get a copy of all items."""
        with self._lock:
            return self._items.copy()

    def set_all(self, items: List[T]):
        """Replace all items."""
        with self._lock:
            self._items = items.copy()

    def append(self, item: T):
        """Append an item."""
        with self._lock:
            self._items.append(item)

    def extend(self, items: List[T]):
        """Extend with multiple items."""
        with self._lock:
            self._items.extend(items)

    def clear(self):
        """Clear all items."""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """Get length."""
        with self._lock:
            return len(self._items)

    def filter(self, predicate: Callable[[T], bool]) -> List[T]:
        """Filter items and return a copy."""
        with self._lock:
            return [item for item in self._items if predicate(item)]

    def update_item(self, predicate: Callable[[T], bool], updater: Callable[[T], T]):
        """
        Update items matching a predicate.

        :param predicate: Function to identify items to update
        :param updater: Function to modify the item
        """
        with self._lock:
            for i, item in enumerate(self._items):
                if predicate(item):
                    self._items[i] = updater(item)


class PackageStateManager:
    """
    Thread-safe manager for package state in the TUI.

    Handles concurrent updates to package information from multiple threads.
    """

    def __init__(self):
        """Initialize the package state manager."""
        self._lock = threading.RLock()
        self._packages: List[Dict[str, Any]] = []
        self._row_mapping: Dict[str, int] = {}

    def set_packages(self, packages: List[Dict[str, Any]]):
        """
        Set all packages and rebuild row mapping.

        :param packages: List of package dictionaries
        """
        with self._lock:
            self._packages = [pkg.copy() for pkg in packages]
            self._rebuild_row_mapping()

    def get_packages(self) -> List[Dict[str, Any]]:
        """Get a copy of all packages."""
        with self._lock:
            return [pkg.copy() for pkg in self._packages]

    def get_package(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific package by name.

        :param name: Package name
        :returns: Package dict or None
        """
        with self._lock:
            for pkg in self._packages:
                if pkg.get('name') == name:
                    return pkg.copy()
            return None

    def update_package(self, name: str, updates: Dict[str, Any]):
        """
        Update a specific package's fields.

        :param name: Package name
        :param updates: Dictionary of fields to update
        """
        with self._lock:
            for pkg in self._packages:
                if pkg.get('name') == name:
                    pkg.update(updates)
                    break

    def get_row_index(self, name: str) -> Optional[int]:
        """
        Get row index for a package.

        :param name: Package name
        :returns: Row index or None
        """
        with self._lock:
            return self._row_mapping.get(name)

    def _rebuild_row_mapping(self):
        """Rebuild the row mapping (call with lock held)."""
        self._row_mapping = {
            pkg['name']: i
            for i, pkg in enumerate(self._packages)
        }

    def get_filtered(self, predicate: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        """
        Get packages matching a predicate.

        :param predicate: Function to filter packages
        :returns: List of matching packages (copies)
        """
        with self._lock:
            return [
                pkg.copy()
                for pkg in self._packages
                if predicate(pkg)
            ]

    def count(self, predicate: Optional[Callable[[Dict[str, Any]], bool]] = None) -> int:
        """
        Count packages, optionally matching a predicate.

        :param predicate: Optional filter function
        :returns: Count of packages
        """
        with self._lock:
            if predicate is None:
                return len(self._packages)
            return sum(1 for pkg in self._packages if predicate(pkg))
