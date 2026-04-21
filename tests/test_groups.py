"""Tests for groups module."""

import sys
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from pipu_cli.groups import (
    load_groups,
    save_groups,
    add_environment,
    remove_environment,
    delete_group,
    list_groups,
    get_group,
    validate_group_name,
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
        """Current Python executable passes validation and returns resolved path."""
        path = validate_python_path(sys.executable)
        assert isinstance(path, str)
        assert path == str(Path(sys.executable).resolve())

    def test_validate_nonexistent_path(self):
        """Non-existent path raises ClickException."""
        with pytest.raises(click.ClickException):
            validate_python_path("/nonexistent/python")

    def test_validate_python_path_resolves_symlinks(self, tmp_path):
        """Symlinks are resolved to their canonical target."""
        real = tmp_path / "real_python"
        real.write_text("#!/bin/sh\nexit 0\n")
        real.chmod(0o755)
        link = tmp_path / "link_python"
        link.symlink_to(real)
        resolved = validate_python_path(str(link))
        assert Path(resolved) == real.resolve()

    def test_validate_python_path_rejects_missing(self, tmp_path):
        """Missing paths raise ClickException."""
        with pytest.raises(click.ClickException):
            validate_python_path(str(tmp_path / "does_not_exist"))

    def test_validate_python_path_rejects_directory(self, tmp_path):
        """Directories are rejected even though they are traversable (x bit)."""
        with pytest.raises(click.ClickException, match="Not a file"):
            validate_python_path(str(tmp_path))


class TestValidateGroupName:
    """Tests for validate_group_name."""

    @pytest.mark.parametrize(
        "bad",
        [
            "has space",
            "slash/name",
            "semi;colon",
            "",
            "weird*",
            "data\nsci",  # guards against re.match (matches at start without $)
            ".",
            "-",
        ],
    )
    def test_reject_invalid_group_name(self, bad):
        """Invalid group names raise ClickException."""
        with pytest.raises(click.ClickException):
            validate_group_name(bad)

    @pytest.mark.parametrize("good", ["data", "data-sci", "data_sci", "data.sci", "d1", "A-B.C_1"])
    def test_accept_valid_group_name(self, good):
        """Valid group names pass without raising."""
        validate_group_name(good)  # no raise
