"""Tests for configuration file support."""

import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from pipu_cli.config_file import find_config_file, load_config, get_config_value


def test_load_config_from_pipu_toml(tmp_path):
    """Test loading config from .pipu.toml."""
    config_content = b'''
timeout = 30
exclude = ["numpy", "pandas"]
pre = true
'''
    config_file = tmp_path / ".pipu.toml"
    config_file.write_bytes(config_content)

    config = load_config(config_file)

    assert config["timeout"] == 30
    assert config["exclude"] == ["numpy", "pandas"]
    assert config["pre"] is True


def test_get_config_value_with_default():
    """Test getting config value with default."""
    config = {"timeout": 30}

    assert get_config_value(config, "timeout", 10) == 30
    assert get_config_value(config, "missing", 10) == 10


def test_load_config_from_pyproject_toml(tmp_path):
    """Test loading config from pyproject.toml [tool.pipu] section."""
    config_content = b'''
[tool.pipu]
timeout = 45
exclude = ["requests"]
pre = false
'''
    config_file = tmp_path / "pyproject.toml"
    config_file.write_bytes(config_content)

    config = load_config(config_file)

    assert config["timeout"] == 45
    assert config["exclude"] == ["requests"]
    assert config["pre"] is False


def test_load_config_nonexistent_file():
    """Test loading config from nonexistent file returns empty dict."""
    config = load_config(Path("/nonexistent/file.toml"))
    assert config == {}


def test_load_config_invalid_toml(tmp_path):
    """Test loading invalid TOML returns empty dict."""
    config_file = tmp_path / "invalid.toml"
    config_file.write_text("this is not valid TOML {[}")

    config = load_config(config_file)
    assert config == {}


def test_find_config_file_local_pipu_toml(tmp_path, monkeypatch):
    """Test finding .pipu.toml in current directory."""
    monkeypatch.chdir(tmp_path)

    # Create .pipu.toml
    pipu_config = tmp_path / ".pipu.toml"
    pipu_config.write_text("timeout = 30")

    config_path = find_config_file()
    assert config_path == Path(".pipu.toml")


def test_find_config_file_pyproject_toml(tmp_path, monkeypatch):
    """Test finding pyproject.toml with [tool.pipu] section."""
    monkeypatch.chdir(tmp_path)

    # Create pyproject.toml with [tool.pipu]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'[tool.pipu]\ntimeout = 30')

    config_path = find_config_file()
    assert config_path == Path("pyproject.toml")


def test_find_config_file_pyproject_without_pipu_section(tmp_path, monkeypatch):
    """Test that pyproject.toml without [tool.pipu] is not returned."""
    monkeypatch.chdir(tmp_path)

    # Create pyproject.toml without [tool.pipu]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'[tool.other]\nvalue = 1')

    config_path = find_config_file()
    assert config_path is None


def test_find_config_file_user_config(tmp_path, monkeypatch):
    """Test finding ~/.config/pipu/config.toml."""
    monkeypatch.chdir(tmp_path)

    # Mock Path.home()
    with patch('pipu_cli.config_file.Path.home') as mock_home:
        mock_home.return_value = tmp_path

        # Create user config
        user_config_dir = tmp_path / ".config" / "pipu"
        user_config_dir.mkdir(parents=True)
        user_config = user_config_dir / "config.toml"
        user_config.write_text("timeout = 60")

        config_path = find_config_file()
        assert config_path == user_config


def test_find_config_file_priority(tmp_path, monkeypatch):
    """Test config file priority: .pipu.toml > pyproject.toml > ~/.config/pipu/config.toml."""
    monkeypatch.chdir(tmp_path)

    # Create all three config files
    pipu_config = tmp_path / ".pipu.toml"
    pipu_config.write_text("timeout = 30")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'[tool.pipu]\ntimeout = 45')

    with patch('pipu_cli.config_file.Path.home') as mock_home:
        mock_home.return_value = tmp_path
        user_config_dir = tmp_path / ".config" / "pipu"
        user_config_dir.mkdir(parents=True)
        user_config = user_config_dir / "config.toml"
        user_config.write_text("timeout = 60")

        # Should return .pipu.toml (highest priority)
        config_path = find_config_file()
        assert config_path == Path(".pipu.toml")


def test_find_config_file_none_found(tmp_path, monkeypatch):
    """Test that None is returned when no config files exist."""
    monkeypatch.chdir(tmp_path)

    with patch('pipu_cli.config_file.Path.home') as mock_home:
        mock_home.return_value = tmp_path
        config_path = find_config_file()
        assert config_path is None


def test_get_config_value_missing_key():
    """Test getting value for missing key returns default."""
    config = {"timeout": 30}

    result = get_config_value(config, "nonexistent", "default_value")
    assert result == "default_value"


def test_get_config_value_none_default():
    """Test getting value with None as default."""
    config = {"timeout": 30}

    result = get_config_value(config, "missing")
    assert result is None


def test_load_config_with_various_types(tmp_path):
    """Test loading config with various data types."""
    config_content = b'''
timeout = 30
exclude = ["pkg1", "pkg2"]
pre = true
debug = false
retries = 3
delay = 1.5
'''
    config_file = tmp_path / ".pipu.toml"
    config_file.write_bytes(config_content)

    config = load_config(config_file)

    assert isinstance(config["timeout"], int)
    assert isinstance(config["exclude"], list)
    assert isinstance(config["pre"], bool)
    assert isinstance(config["debug"], bool)
    assert isinstance(config["retries"], int)
    assert isinstance(config["delay"], float)
