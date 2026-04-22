"""Tests for rollback functionality."""

import json
import subprocess
from unittest.mock import patch

from pipu_cli.rollback import save_state, get_latest_state, rollback_to_state


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


def test_rollback_to_state_surfaces_pip_failures(monkeypatch):
    """rollback_to_state returns failures with reasons; it no longer silently drops."""
    state = {"packages": [
        {"name": "requests", "version": "2.30.0"},
        {"name": "broken-pkg", "version": "9.9.9"},
    ]}
    calls = []

    def fake_run(cmd, check, capture_output):
        calls.append(cmd)
        if "broken-pkg==9.9.9" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"no such distribution")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("pipu_cli.rollback.subprocess.run", fake_run)

    result = rollback_to_state(state, dry_run=False)

    assert [p.spec for p in result.succeeded] == ["requests==2.30.0"]
    assert len(result.failed) == 1
    assert result.failed[0].spec == "broken-pkg==9.9.9"
    assert "no such distribution" in result.failed[0].reason or "pip exit code 1" in result.failed[0].reason
