"""Tests for download-then-install pipeline."""

from unittest.mock import patch, MagicMock

import pytest

from packaging.version import Version

from pipu_cli.download import download_packages, install_from_local, download_packages_for_group


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

    def test_download_os_error_surfaces_reason(self, tmp_path):
        """OSError from subprocess.run (e.g. missing interpreter) reaches the progress callback."""
        failures = []

        def fake_run(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "/nope/python")

        def callback(spec, success, error_msg):
            if not success:
                failures.append((spec, error_msg))

        with patch("pipu_cli.download.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="Failed to download"):
                download_packages(
                    specs=["requests==2.31.0"],
                    dest_dir=tmp_path,
                    progress_callback=callback,
                )

        assert len(failures) == 1
        assert failures[0][0] == "requests==2.31.0"
        assert "No such file or directory" in failures[0][1] or "/nope/python" in failures[0][1]


class TestInstallFromLocal:
    """Tests for install_from_local function."""

    def test_install_single_package(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("Successfully installed requests-2.31.0", "")

        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

            cmd = mock_run.call_args[0][0]
            assert "--find-links" in cmd
            assert "--no-deps" in cmd
            assert "requests==2.31.0" in cmd
            assert len(results) == 1
            assert results[0].upgraded is True
            assert results[0].version == Version("2.31.0")
            assert results[0].previous_version == Version("2.28.0")

    def test_install_with_python_path(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run, \
             patch("pipu_cli.download._get_remote_package_versions", side_effect=[pre_versions, post_versions]) as mock_remote:
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                python_path="/usr/bin/python3",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/bin/python3"
            assert mock_remote.call_count == 2
            assert results[0].upgraded is True

    def test_install_calls_progress_callback(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        callback = MagicMock()
        with patch("pipu_cli.download.subprocess.run", return_value=mock_process), \
             patch("pipu_cli.download._get_local_package_versions", return_value={}):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                progress_callback=callback,
            )
            callback.assert_called_once_with("requests==2.31.0")

    def test_install_failure_marks_not_upgraded(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate.return_value = ("", "ERROR: install failed")

        pre_versions = {"requests": Version("2.28.0")}

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process), \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, pre_versions]):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )
            assert len(results) == 1
            assert results[0].upgraded is False
            assert results[0].failure_reason is not None

    def test_install_empty_specs_returns_empty(self, tmp_path):
        results = install_from_local(dest_dir=tmp_path, specs=[])
        assert results == []


class TestDownloadPackagesForGroup:
    """Tests for group download deduplication."""

    def test_deduplicates_same_version(self, tmp_path):
        """requests==2.31.0 needed by both main and ml should download once."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        env_plans = {
            "main": ["requests==2.31.0", "numpy==1.26.0"],
            "ml": ["requests==2.31.0", "rich==13.7.0"],
        }

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run:
            download_packages_for_group(env_plans, tmp_path)
            # requests==2.31.0 appears in both, so 3 unique specs total
            assert mock_run.call_count == 3

    def test_different_versions_both_downloaded(self, tmp_path):
        """numpy==1.25 and numpy==1.26 are different specs -- both download."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        env_plans = {
            "main": ["numpy==1.26.0"],
            "ml": ["numpy==1.25.0"],
        }

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process) as mock_run:
            download_packages_for_group(env_plans, tmp_path)
            assert mock_run.call_count == 2

    def test_calls_progress_callback(self, tmp_path):
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        callback = MagicMock()
        env_plans = {"main": ["requests==2.31.0"]}

        with patch("pipu_cli.download.subprocess.run", return_value=mock_process):
            download_packages_for_group(env_plans, tmp_path, progress_callback=callback)
            callback.assert_called_once_with("requests==2.31.0", True, "")

    def test_empty_plans_returns_empty(self, tmp_path):
        result = download_packages_for_group({}, tmp_path)
        assert result == []
