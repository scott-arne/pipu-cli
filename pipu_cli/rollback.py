"""Rollback functionality for pipu."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipu_cli.package_management import UpgradedPackage


ROLLBACK_DIR = Path.home() / ".pipu" / "rollback"


def save_state(packages: List[Dict[str, str]], description: str = "") -> Path:
    """Save current package state for potential rollback.

    :param packages: List of dicts with 'name' and 'version' keys
    :param description: Optional description of the state
    :returns: Path to saved state file
    """
    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_file = ROLLBACK_DIR / f"state_{timestamp}.json"

    state = {
        "timestamp": timestamp,
        "description": description,
        "packages": packages
    }

    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

    return state_file


def get_latest_state() -> Optional[Dict[str, Any]]:
    """Get the most recent saved state.

    :returns: State dictionary or None if no states saved
    """
    if not ROLLBACK_DIR.exists():
        return None

    state_files = sorted(ROLLBACK_DIR.glob("state_*.json"), reverse=True)

    if not state_files:
        return None

    with open(state_files[0], 'r') as f:
        return json.load(f)


def rollback_to_state(state: Dict[str, Any], dry_run: bool = False) -> List[str]:
    """Rollback packages to a saved state.

    :param state: State dictionary from get_latest_state()
    :param dry_run: If True, only show what would be done
    :returns: List of packages that were rolled back
    """
    packages = state.get("packages", [])
    rolled_back = []

    for pkg in packages:
        name = pkg["name"]
        version = pkg["version"]

        if dry_run:
            rolled_back.append(f"{name}=={version}")
            continue

        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', f'{name}=={version}'],
                check=True,
                capture_output=True
            )
            rolled_back.append(f"{name}=={version}")
        except subprocess.CalledProcessError:
            pass

    return rolled_back


def list_states() -> List[Dict[str, Any]]:
    """List all saved states.

    :returns: List of state summaries
    """
    if not ROLLBACK_DIR.exists():
        return []

    states = []
    for state_file in sorted(ROLLBACK_DIR.glob("state_*.json"), reverse=True):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
            states.append({
                "file": state_file.name,
                "timestamp": state.get("timestamp", "unknown"),
                "description": state.get("description", ""),
                "package_count": len(state.get("packages", []))
            })
        except Exception:
            pass

    return states
