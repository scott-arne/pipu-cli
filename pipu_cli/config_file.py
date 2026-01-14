"""Configuration file support for pipu."""

from pathlib import Path
from typing import Dict, Any, Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


def find_config_file() -> Optional[Path]:
    """Find the pipu configuration file.

    Searches in order:
    1. .pipu.toml in current directory
    2. pyproject.toml in current directory (looks for [tool.pipu] section)
    3. ~/.config/pipu/config.toml

    :returns: Path to config file or None if not found
    """
    # Check current directory for .pipu.toml
    local_config = Path(".pipu.toml")
    if local_config.exists():
        return local_config

    # Check pyproject.toml for [tool.pipu] section
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            if "tool" in data and "pipu" in data["tool"]:
                return pyproject
        except Exception:
            pass

    # Check user config directory
    user_config = Path.home() / ".config" / "pipu" / "config.toml"
    if user_config.exists():
        return user_config

    return None


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load pipu configuration from file.

    :param config_path: Optional explicit path to config file
    :returns: Configuration dictionary
    """
    if config_path is None:
        config_path = find_config_file()

    if config_path is None:
        return {}

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        # Extract [tool.pipu] section from pyproject.toml
        if config_path.name == "pyproject.toml":
            return data.get("tool", {}).get("pipu", {})

        return data
    except Exception:
        return {}


def get_config_value(config: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a configuration value with default.

    :param config: Configuration dictionary
    :param key: Key to look up
    :param default: Default value if key not found
    :returns: Configuration value or default
    """
    return config.get(key, default)
