"""Tests for download-then-install pipeline."""

from unittest.mock import patch, MagicMock

import pytest

from pipu_cli.download import download_packages


class TestDownloadPackages:
    """Tests for download_packages function."""

    def test_download_single_package(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        # Create a fake wheel file to simulate download
        fake_wheel = tmp_path / "requests-2.31.0-py3-none-any.whl"
        fake_wheel.touch()

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run:
            result = download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
            )

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "download" in cmd
            assert "--dest" in cmd
            assert "--no-deps" in cmd
            assert "requests==2.31.0" in cmd
            assert len(result) == 1

    def test_download_with_python_path(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run:
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
                python_path="/usr/bin/python3",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/python3"

    def test_download_with_pre_flag(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run:
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
                pre=True,
            )
            cmd = mock_run.call_args[0][0]
            assert "--pre" in cmd

    def test_download_calls_progress_callback(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        callback = MagicMock()
        with patch("pipu_cli.download.subprocess.run", return_value=mock_process):
            download_packages(
                specs=["requests==2.31.0", "rich==13.7.0"],
                dest_dir=tmp_path,
                progress_callback=callback,
            )
            assert callback.call_count == 2

    def test_download_failure_raises(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate.return_value = ("", "ERROR: No matching distribution")

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process):
            with pytest.raises(RuntimeError, match="Failed to download"):
                download_packages(
                    specs=["nonexistent-pkg==1.0.0"],
                    dest_dir=tmp_path,
                )

    def test_download_empty_specs_returns_empty(self, tmp_path):
        result = download_packages(specs=[], dest_dir=tmp_path)
        assert result == []
