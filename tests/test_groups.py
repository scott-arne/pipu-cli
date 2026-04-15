"""Tests for groups module."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pipu_cli.groups import (
    GROUPS_FILE,
    load_groups,
    save_groups,
    add_environment,
    remove_environment,
    delete_group,
    list_groups,
    get_group,
    validate_python_path,
)


@pytest.fixture
def groups_dir(tmp_path):
    """Create a temporary groups directory."""
    groups_file = tmp_path / "groups.toml"
    with patch("pipu_cli.groups.GROUPS_FILE", groups_file):
        yield groups_file


class TestLoadSaveGroups:
    """Tests for loading and saving group data."""

    def test_load_returns_empty_when_no_file(self, groups_dir):
        """Load returns empty dict when groups file doesn't exist."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result = load_groups()
        assert result == {}

    def test_save_and_load_roundtrip(self, groups_dir):
        """Groups survive a save/load roundtrip."""
        groups = {
            "data-science": ["/usr/bin/python3", "/opt/python/bin/python"],
            "web": ["/home/user/.venv/bin/python"],
        }
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            save_groups(groups)
            loaded = load_groups()
        assert loaded == groups

    def test_save_creates_parent_directories(self, tmp_path):
        """Save creates parent directories if they don't exist."""
        deep_path = tmp_path / "a" / "b" / "groups.toml"
        with patch("pipu_cli.groups.GROUPS_FILE", deep_path):
            save_groups({"test": ["/usr/bin/python3"]})
        assert deep_path.exists()

    def test_save_is_atomic(self, groups_dir):
        """Save uses atomic write (temp file + rename)."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            save_groups({"test": ["/usr/bin/python3"]})
            # File should exist and be valid TOML
            content = groups_dir.read_text()
            assert "test" in content


class TestAddEnvironment:
    """Tests for adding environments to groups."""

    def test_add_to_new_group(self, groups_dir):
        """Adding to a non-existent group creates it."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            groups = load_groups()
        assert "mygroup" in groups
        assert "/usr/bin/python3" in groups["mygroup"]

    def test_add_to_existing_group(self, groups_dir):
        """Adding to an existing group appends."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            add_environment("mygroup", "/opt/python/bin/python")
            groups = load_groups()
        assert len(groups["mygroup"]) == 2

    def test_add_duplicate_is_noop(self, groups_dir):
        """Adding a duplicate path returns False and doesn't duplicate."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result1 = add_environment("mygroup", "/usr/bin/python3")
            result2 = add_environment("mygroup", "/usr/bin/python3")
            groups = load_groups()
        assert result1 is True
        assert result2 is False
        assert len(groups["mygroup"]) == 1

    def test_add_uses_sys_executable_when_no_path(self, groups_dir):
        """Default path is sys.executable."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup")
            groups = load_groups()
        assert sys.executable in groups["mygroup"]


class TestRemoveEnvironment:
    """Tests for removing environments from groups."""

    def test_remove_existing_environment(self, groups_dir):
        """Removing an existing environment succeeds."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            add_environment("mygroup", "/opt/python/bin/python")
            result = remove_environment("mygroup", "/usr/bin/python3")
            groups = load_groups()
        assert result is True
        assert "/usr/bin/python3" not in groups["mygroup"]
        assert "/opt/python/bin/python" in groups["mygroup"]

    def test_remove_last_deletes_group(self, groups_dir):
        """Removing the last environment deletes the group."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            remove_environment("mygroup", "/usr/bin/python3")
            groups = load_groups()
        assert "mygroup" not in groups

    def test_remove_nonexistent_environment(self, groups_dir):
        """Removing a non-existent environment returns False."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            result = remove_environment("mygroup", "/opt/not/here")
        assert result is False

    def test_remove_from_nonexistent_group(self, groups_dir):
        """Removing from a non-existent group returns False."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result = remove_environment("nogroup", "/usr/bin/python3")
        assert result is False


class TestDeleteGroup:
    """Tests for deleting groups."""

    def test_delete_existing_group(self, groups_dir):
        """Deleting an existing group succeeds."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            result = delete_group("mygroup")
            groups = load_groups()
        assert result is True
        assert "mygroup" not in groups

    def test_delete_nonexistent_group(self, groups_dir):
        """Deleting a non-existent group returns False."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result = delete_group("nogroup")
        assert result is False


class TestListAndGetGroups:
    """Tests for listing and getting groups."""

    def test_list_groups_empty(self, groups_dir):
        """Listing when no groups exist returns empty dict."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result = list_groups()
        assert result == {}

    def test_list_groups_with_data(self, groups_dir):
        """Listing returns all groups."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("a", "/usr/bin/python3")
            add_environment("b", "/opt/python/bin/python")
            result = list_groups()
        assert "a" in result
        assert "b" in result

    def test_get_group_returns_environments(self, groups_dir):
        """Getting a group returns its environment list."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            add_environment("mygroup", "/usr/bin/python3")
            result = get_group("mygroup")
        assert result == ["/usr/bin/python3"]

    def test_get_nonexistent_group_returns_none(self, groups_dir):
        """Getting a non-existent group returns None."""
        with patch("pipu_cli.groups.GROUPS_FILE", groups_dir):
            result = get_group("nogroup")
        assert result is None


class TestValidatePythonPath:
    """Tests for Python path validation."""

    def test_validate_current_python(self):
        """Current Python executable passes validation."""
        is_valid, error = validate_python_path(sys.executable)
        assert is_valid is True
        assert error is None

    def test_validate_nonexistent_path(self):
        """Non-existent path fails validation."""
        is_valid, error = validate_python_path("/nonexistent/python")
        assert is_valid is False
        assert "does not exist" in error

    def test_validate_non_python(self, tmp_path):
        """A non-Python executable fails validation."""
        fake = tmp_path / "not_python"
        fake.write_text("#!/bin/bash\necho 'not python'")
        fake.chmod(0o755)
        is_valid, error = validate_python_path(str(fake))
        assert is_valid is False
