"""Rollback functionality for pipu."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional



ROLLBACK_DIR = Path.home() / ".pipu" / "rollback"


@dataclass(frozen=True)
class PackageRollbackOutcome:
    """One package's rollback result.

    :param spec: The ``name==version`` string that was attempted.
    :param reason: ``None`` on success; a human-readable failure string otherwise.
    """
    spec: str
    reason: Optional[str] = None


@dataclass
class RollbackResult:
    """Aggregate result from :func:`rollback_to_state`.

    :param succeeded: Packages successfully reinstalled at the saved version.
    :param failed: Packages whose ``pip install`` raised; the ``reason`` field
        carries the stderr/returncode summary.
    """
    succeeded: List[PackageRollbackOutcome] = field(default_factory=list)
    failed: List[PackageRollbackOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


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


def rollback_to_state(state: Dict[str, Any], dry_run: bool = False) -> RollbackResult:
    """Rollback packages to a saved state.

    :param state: State dictionary from :func:`get_latest_state`.
    :param dry_run: If True, only report what would be done; never invoke pip.
    :returns: :class:`RollbackResult` with per-package success / failure detail.
    """
    result = RollbackResult()
    for pkg in state.get("packages", []):
        name = pkg["name"]
        version = pkg["version"]
        spec = f"{name}=={version}"
        if dry_run:
            result.succeeded.append(PackageRollbackOutcome(spec=spec))
            continue
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", spec],
                check=True,
                capture_output=True,
            )
            result.succeeded.append(PackageRollbackOutcome(spec=spec))
        except subprocess.CalledProcessError as e:
            if isinstance(e.stderr, (bytes, bytearray)):
                stderr = e.stderr.decode(errors="replace").strip()
            else:
                stderr = (e.stderr or "").strip()
            reason = (
                f"pip exit code {e.returncode}: {stderr[:500]}"
                if stderr else f"pip exit code {e.returncode}"
            )
            result.failed.append(PackageRollbackOutcome(spec=spec, reason=reason))
    return result


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
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            pass

    return states
