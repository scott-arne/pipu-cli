"""
Tests for caching mechanisms in pipu.

This module tests caching functionality for expensive operations like
detecting editable packages to avoid repeated subprocess calls.
"""
import time
from unittest.mock import Mock, patch
import pytest


class TestEditablePackagesCaching:
    """Test the caching mechanism for get_editable_packages()."""

    def test_cache_returns_same_result_within_ttl(self):
        """Test that cache returns same result within TTL without calling subprocess."""
        from pipu.internals import get_editable_packages
        import pipu.internals as internals

        # Clear cache first
        internals._editable_packages_cache.invalidate()

        with patch('subprocess.run') as mock_run:
            # First call - should hit subprocess
            mock_result = Mock()
            mock_result.stdout = "Package    Version Location\n----------  ------- --------\ntest-pkg   1.0.0   /path/to/test\n"
            mock_run.return_value = mock_result

            result1 = get_editable_packages()
            assert mock_run.call_count == 1
            assert 'test-pkg' in result1

            # Second call within TTL - should use cache, not hit subprocess
            result2 = get_editable_packages()
            assert mock_run.call_count == 1  # Still 1, not 2
            assert result1 == result2

    def test_cache_expires_after_ttl(self):
        """Test that cache expires and refreshes after TTL."""
        from pipu.internals import get_editable_packages
        import pipu.internals as internals
        from pipu.thread_safe import ThreadSafeCache

        # Create a new cache with short TTL for testing
        original_cache = internals._editable_packages_cache
        internals._editable_packages_cache = ThreadSafeCache(ttl=0.1)  # 100ms

        try:
            with patch('subprocess.run') as mock_run:
                mock_result = Mock()
                mock_result.stdout = "Package    Version Location\n----------  ------- --------\ntest-pkg   1.0.0   /path/to/test\n"
                mock_run.return_value = mock_result

                # First call
                result1 = get_editable_packages()
                assert mock_run.call_count == 1

                # Wait for cache to expire
                time.sleep(0.15)

                # Second call after TTL - should hit subprocess again
                result2 = get_editable_packages()
                assert mock_run.call_count == 2
                assert result1 == result2
        finally:
            # Restore original cache
            internals._editable_packages_cache = original_cache

    def test_cache_returns_copy_not_reference(self):
        """Test that cache returns a copy to prevent external modifications."""
        from pipu.internals import get_editable_packages
        import pipu.internals as internals

        # Clear cache first
        internals._editable_packages_cache.invalidate()

        with patch('subprocess.run') as mock_run:
            mock_result = Mock()
            mock_result.stdout = "Package    Version Location\n----------  ------- --------\ntest-pkg   1.0.0   /path/to/test\n"
            mock_run.return_value = mock_result

            result1 = get_editable_packages()
            result1['new-package'] = '/path/to/new'  # Modify returned dict

            # Get cached result
            result2 = get_editable_packages()

            # Original cache should not be modified
            assert 'new-package' not in result2
            assert 'test-pkg' in result2

    def test_cache_handles_subprocess_errors(self):
        """Test that cache handles subprocess errors gracefully."""
        from pipu.internals import get_editable_packages
        import pipu.internals as internals
        import subprocess

        # Clear cache first
        internals._editable_packages_cache.invalidate()

        with patch('subprocess.run') as mock_run:
            # Simulate subprocess error
            mock_run.side_effect = subprocess.CalledProcessError(1, 'pip')

            result = get_editable_packages()

            # Should return empty dict on error
            assert result == {}
