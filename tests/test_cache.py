"""Tests for cache module."""

import json
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock

from packaging.version import Version

from pipu_cli.cache import (
    get_environment_id,
    get_cache_dir,
    get_cache_path,
    load_cache,
    save_cache,
    is_cache_fresh,
    get_cache_age_seconds,
    format_cache_age,
    clear_cache,
    clear_all_caches,
    build_version_cache,
    get_cache_info,
)
from pipu_cli.package_management import InstalledPackage


class TestEnvironmentId:
    """Tests for environment identification."""

    def test_get_environment_id_returns_hash(self):
        """Test that get_environment_id returns a hash."""
        env_id = get_environment_id()
        assert isinstance(env_id, str)
        assert len(env_id) == 12
        # Should be hexadecimal
        assert all(c in '0123456789abcdef' for c in env_id)

    def test_get_environment_id_consistent(self):
        """Test that environment ID is consistent across calls."""
        id1 = get_environment_id()
        id2 = get_environment_id()
        assert id1 == id2

    def test_get_environment_id_changes_with_executable(self):
        """Test that different executables produce different IDs."""
        real_id = get_environment_id()

        with patch.object(sys, 'executable', '/different/python'):
            different_id = get_environment_id()

        assert real_id != different_id


class TestCachePaths:
    """Tests for cache path functions."""

    def test_get_cache_dir_includes_environment_id(self, tmp_path):
        """Test that cache dir includes environment ID."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_dir = get_cache_dir()
            env_id = get_environment_id()
            assert str(cache_dir).endswith(env_id)

    def test_get_cache_path_is_versions_json(self, tmp_path):
        """Test that cache path is versions.json."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_path = get_cache_path()
            assert cache_path.name == "versions.json"


class TestLoadCache:
    """Tests for load_cache function."""

    def test_load_cache_returns_none_when_not_exists(self, tmp_path):
        """Test that load_cache returns None when cache doesn't exist."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            result = load_cache()
            assert result is None

    def test_load_cache_returns_none_for_invalid_json(self, tmp_path):
        """Test that load_cache returns None for invalid JSON."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_path = get_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("not valid json")

            result = load_cache()
            assert result is None

    def test_load_cache_returns_none_for_wrong_environment(self, tmp_path):
        """Test that load_cache returns None if environment ID doesn't match."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_path = get_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Write cache with different environment ID
            cache_data = {
                "environment_id": "wrongenvid123",
                "python_executable": "/wrong/python",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "include_prereleases": False,
                "latest_versions": {}
            }
            cache_path.write_text(json.dumps(cache_data))

            result = load_cache()
            assert result is None

    def test_load_cache_success(self, tmp_path):
        """Test successful cache load."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_path = get_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            env_id = get_environment_id()
            now = datetime.now(timezone.utc).isoformat()
            cache_data = {
                "environment_id": env_id,
                "python_executable": sys.executable,
                "updated_at": now,
                "include_prereleases": False,
                "latest_versions": {
                    "requests": "2.31.0",
                    "numpy": "1.26.0"
                }
            }
            cache_path.write_text(json.dumps(cache_data))

            result = load_cache()
            assert result is not None
            assert result.environment_id == env_id
            assert "requests" in result.latest_versions
            assert result.latest_versions["requests"] == "2.31.0"


class TestSaveCache:
    """Tests for save_cache function."""

    def test_save_cache_creates_directory(self, tmp_path):
        """Test that save_cache creates the cache directory."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            latest_versions = {"test": "1.0.0"}
            cache_path = save_cache(latest_versions)

            assert cache_path.exists()
            assert cache_path.parent.exists()

    def test_save_cache_writes_valid_json(self, tmp_path):
        """Test that save_cache writes valid JSON."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            latest_versions = {
                "requests": "2.31.0",
                "numpy": "1.26.0"
            }
            cache_path = save_cache(latest_versions)

            data = json.loads(cache_path.read_text())
            assert "environment_id" in data
            assert "python_executable" in data
            assert "updated_at" in data
            assert "include_prereleases" in data
            assert "latest_versions" in data
            assert data["latest_versions"]["requests"] == "2.31.0"

    def test_save_cache_with_prereleases_flag(self, tmp_path):
        """Test that save_cache stores prereleases flag."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            latest_versions = {"test": "1.0.0"}
            save_cache(latest_versions, include_prereleases=True)

            loaded = load_cache()
            assert loaded is not None
            assert loaded.include_prereleases is True

    def test_save_and_load_roundtrip(self, tmp_path):
        """Test that saved cache can be loaded back."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            latest_versions = {
                "numpy": "1.26.0",
                "requests": "2.31.0"
            }
            save_cache(latest_versions)
            loaded = load_cache()

            assert loaded is not None
            assert "numpy" in loaded.latest_versions
            assert loaded.latest_versions["numpy"] == "1.26.0"


class TestCacheFreshness:
    """Tests for cache freshness functions."""

    def test_is_cache_fresh_returns_false_when_no_cache(self, tmp_path):
        """Test is_cache_fresh returns False when no cache exists."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            assert is_cache_fresh() is False

    def test_is_cache_fresh_returns_true_for_recent_cache(self, tmp_path):
        """Test is_cache_fresh returns True for fresh cache."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            save_cache({"test": "1.0.0"})
            assert is_cache_fresh(ttl_seconds=3600) is True

    def test_is_cache_fresh_returns_false_for_old_cache(self, tmp_path):
        """Test is_cache_fresh returns False for stale cache."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            cache_path = get_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            env_id = get_environment_id()
            # Set updated_at to 2 hours ago
            old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            cache_data = {
                "environment_id": env_id,
                "python_executable": sys.executable,
                "updated_at": old_time,
                "include_prereleases": False,
                "latest_versions": {}
            }
            cache_path.write_text(json.dumps(cache_data))

            # With 1 hour TTL, cache should be stale
            assert is_cache_fresh(ttl_seconds=3600) is False

    def test_get_cache_age_seconds_returns_none_when_no_cache(self, tmp_path):
        """Test get_cache_age_seconds returns None when no cache."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            assert get_cache_age_seconds() is None

    def test_get_cache_age_seconds_returns_age(self, tmp_path):
        """Test get_cache_age_seconds returns correct age."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            save_cache({"test": "1.0.0"})
            age = get_cache_age_seconds()

            assert age is not None
            assert age >= 0
            assert age < 5  # Should be very recent


class TestFormatCacheAge:
    """Tests for format_cache_age function."""

    def test_format_none(self):
        """Test formatting None returns 'never'."""
        assert format_cache_age(None) == "never"

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_cache_age(30) == "30 seconds ago"

    def test_format_one_minute(self):
        """Test formatting 1 minute."""
        assert format_cache_age(60) == "1 minute ago"

    def test_format_minutes(self):
        """Test formatting multiple minutes."""
        assert format_cache_age(300) == "5 minutes ago"

    def test_format_one_hour(self):
        """Test formatting 1 hour."""
        assert format_cache_age(3600) == "1 hour ago"

    def test_format_hours(self):
        """Test formatting multiple hours."""
        assert format_cache_age(7200) == "2 hours ago"

    def test_format_one_day(self):
        """Test formatting 1 day."""
        assert format_cache_age(86400) == "1 day ago"

    def test_format_days(self):
        """Test formatting multiple days."""
        assert format_cache_age(172800) == "2 days ago"


class TestClearCache:
    """Tests for cache clearing functions."""

    def test_clear_cache_returns_false_when_not_exists(self, tmp_path):
        """Test clear_cache returns False when cache doesn't exist."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            assert clear_cache() is False

    def test_clear_cache_deletes_cache(self, tmp_path):
        """Test clear_cache deletes the cache file."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            save_cache({"test": "1.0.0"})
            cache_path = get_cache_path()
            assert cache_path.exists()

            assert clear_cache() is True
            assert not cache_path.exists()

    def test_clear_all_caches_returns_zero_when_empty(self, tmp_path):
        """Test clear_all_caches returns 0 when no caches exist."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            assert clear_all_caches() == 0

    def test_clear_all_caches_clears_all(self, tmp_path):
        """Test clear_all_caches clears all cache directories."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            # Create multiple cache directories
            for i in range(3):
                cache_dir = tmp_path / f"env{i}"
                cache_dir.mkdir()
                (cache_dir / "versions.json").write_text("{}")

            count = clear_all_caches()
            assert count == 3


class TestBuildVersionCache:
    """Tests for build_version_cache function."""

    def test_build_version_cache(self):
        """Test building cache from version check results."""
        installed = [
            InstalledPackage(
                name="requests",
                version=Version("2.28.0"),
                is_editable=False,
                constrained_dependencies={}
            ),
            InstalledPackage(
                name="numpy",
                version=Version("1.24.0"),
                is_editable=False,
                constrained_dependencies={}
            ),
        ]

        latest_mock_requests = Mock()
        latest_mock_requests.version = Version("2.31.0")

        latest_mock_numpy = Mock()
        latest_mock_numpy.version = Version("1.26.0")

        latest_versions = {
            installed[0]: latest_mock_requests,
            installed[1]: latest_mock_numpy,
        }

        result = build_version_cache(latest_versions)

        assert "requests" in result
        assert result["requests"] == "2.31.0"
        assert "numpy" in result
        assert result["numpy"] == "1.26.0"

    def test_build_version_cache_empty(self):
        """Test building cache with no packages."""
        result = build_version_cache({})
        assert result == {}


class TestGetCacheInfo:
    """Tests for get_cache_info function."""

    def test_get_cache_info_no_cache(self, tmp_path):
        """Test get_cache_info when no cache exists."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            info = get_cache_info()

            assert info["exists"] is False
            assert "environment_id" in info
            assert "python_executable" in info

    def test_get_cache_info_with_cache(self, tmp_path):
        """Test get_cache_info when cache exists."""
        with patch('pipu_cli.cache.CACHE_BASE_DIR', tmp_path):
            save_cache({
                "requests": "2.31.0",
                "numpy": "1.26.0"
            })

            info = get_cache_info()

            assert info["exists"] is True
            assert info["package_count"] == 2
            assert "updated_at" in info
            assert "age_human" in info
            assert "include_prereleases" in info


class TestPythonPathParameter:
    """Tests for python_path parameter in cache functions."""

    def test_get_environment_id_with_python_path(self):
        """Environment ID differs when python_path is provided."""
        default_id = get_environment_id()
        remote_id = get_environment_id(python_path="/some/other/python")
        assert default_id != remote_id

    def test_get_environment_id_python_path_consistent(self):
        """Same python_path always produces the same ID."""
        id1 = get_environment_id(python_path="/some/python")
        id2 = get_environment_id(python_path="/some/python")
        assert id1 == id2

    def test_get_cache_dir_with_python_path(self, tmp_path):
        """Cache dir uses python_path-based env ID when provided."""
        with patch("pipu_cli.cache.CACHE_BASE_DIR", tmp_path):
            default_dir = get_cache_dir()
            remote_dir = get_cache_dir(python_path="/other/python")
        assert default_dir != remote_dir

    def test_save_and_load_with_python_path(self, tmp_path):
        """Cache save/load works with python_path."""
        with patch("pipu_cli.cache.CACHE_BASE_DIR", tmp_path):
            versions = {"requests": "2.31.0"}
            save_cache(versions, python_path="/other/python")
            cache = load_cache(python_path="/other/python")
        assert cache is not None
        assert cache.latest_versions == versions

    def test_separate_caches_per_python_path(self, tmp_path):
        """Different python_paths get separate caches."""
        with patch("pipu_cli.cache.CACHE_BASE_DIR", tmp_path):
            save_cache({"pkg": "1.0"}, python_path="/python/a")
            save_cache({"pkg": "2.0"}, python_path="/python/b")

            cache_a = load_cache(python_path="/python/a")
            cache_b = load_cache(python_path="/python/b")

        assert cache_a.latest_versions["pkg"] == "1.0"  # pyright: ignore[reportOptionalMemberAccess]
        assert cache_b.latest_versions["pkg"] == "2.0"  # pyright: ignore[reportOptionalMemberAccess]

    def test_is_cache_fresh_with_python_path(self, tmp_path):
        """is_cache_fresh works with python_path."""
        with patch("pipu_cli.cache.CACHE_BASE_DIR", tmp_path):
            save_cache({"pkg": "1.0"}, python_path="/other/python")
            assert is_cache_fresh(3600, python_path="/other/python") is True
            # Different python_path has no cache
            assert is_cache_fresh(3600, python_path="/no/cache/here") is False
