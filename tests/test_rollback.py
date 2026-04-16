"""Tests for rollback functionality."""

import json
from unittest.mock import patch

from pipu_cli.rollback import save_state, get_latest_state


def test_save_state(tmp_path):
    """Test saving package state."""
    with patch('pipu_cli.rollback.ROLLBACK_DIR', tmp_path):
        packages = [
            {"name": "requests", "version": "2.28.0"},
            {"name": "numpy", "version": "1.24.0"}
        ]

        state_file = save_state(packages, "Before upgrade")

        assert state_file.exists()

        with open(state_file) as f:
            data = json.load(f)

        assert len(data["packages"]) == 2
        assert data["description"] == "Before upgrade"


def test_get_latest_state(tmp_path):
    """Test getting the most recent state."""
    with patch('pipu_cli.rollback.ROLLBACK_DIR', tmp_path):
        packages = [{"name": "requests", "version": "2.28.0"}]
        save_state(packages, "First")
        save_state(packages, "Second")

        latest = get_latest_state()

        assert latest is not None
        assert latest["description"] == "Second"
