"""Tests for download-then-install pipeline."""

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from packaging.version import Version

from pipu_cli._subprocess import PipResult
from pipu_cli.download import (
    DownloadError,
    download_packages,
    download_packages_for_group,
    install_from_local,
)


class TestDownloadPackages:
    """Tests for download_packages function."""

    def test_download_single_package(self, tmp_path):
        # Create a fake wheel file to simulate download
        fake_wheel = tmp_path / "requests-2.31.0-py3-none-any.whl"
        fake_wheel.touch()

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run:
            result = download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
            )

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "download" in cmd
            assert "--dest" in cmd
            assert "--no-deps" not in cmd
            assert "--timeout" in cmd
            assert "300" in cmd
            assert "--progress-bar" in cmd
            assert "raw" in cmd
            assert "requests==2.31.0" in cmd
            assert mock_run.call_args.kwargs["timeout_mode"] == "idle"
            assert len(result) == 1

    def test_download_with_python_path(self, tmp_path):
        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run:
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
                python_path="/usr/bin/python3",
            )
            assert mock_run.call_args.kwargs["python_path"] == "/usr/bin/python3"

    def test_download_with_pre_flag(self, tmp_path):
        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run:
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
                pre=True,
            )
            cmd = mock_run.call_args[0][0]
            assert "--pre" in cmd

    def test_download_calls_progress_callback(self, tmp_path):
        callback = MagicMock()
        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")):
            download_packages(
                specs=["requests==2.31.0", "rich==13.7.0"],
                dest_dir=tmp_path,
                progress_callback=callback,
            )
            assert callback.call_count == 2

    def test_download_failure_raises(self, tmp_path):
        result = PipResult(
            returncode=1,
            stdout="",
            stderr="ERROR: No matching distribution",
        )

        with patch("pipu_cli.download.run_pip", return_value=result):
            with pytest.raises(DownloadError, match="Failed to download") as exc_info:
                download_packages(
                    specs=["nonexistent-pkg==1.0.0"],
                    dest_dir=tmp_path,
                )
        assert exc_info.value.failed == {
            "nonexistent-pkg==1.0.0": "ERROR: No matching distribution"
        }

    def test_download_idle_timeout_raises_with_clear_reason(self, tmp_path):
        result = PipResult(
            returncode=-1,
            stdout="Progress 1048576 of 10485760\n",
            stderr="",
            timed_out=True,
        )

        with patch("pipu_cli.download.run_pip", return_value=result):
            with pytest.raises(DownloadError, match="Failed to download") as exc_info:
                download_packages(
                    specs=["large-pkg==1.0.0"],
                    dest_dir=tmp_path,
                    timeout=30,
                )

        assert exc_info.value.failed == {
            "large-pkg==1.0.0": "timed out after 30s without progress"
        }

    def test_download_reports_raw_progress_for_package(self, tmp_path):
        updates = []

        def record_update(spec, downloaded, total):
            updates.append((spec, downloaded, total))

        def fake_run(*args, line_callback=None, **kwargs):
            assert line_callback is not None
            line_callback("Progress 1024 of 4096\n")
            line_callback("Progress 4096 of 4096\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages(
                specs=["large-pkg==1.0.0"],
                dest_dir=tmp_path,
                download_progress_callback=record_update,
            )

        assert updates == [
            ("large-pkg==1.0.0", 1024, 4096),
            ("large-pkg==1.0.0", 4096, 4096),
        ]

    def test_download_ignores_non_progress_lines_for_package_progress(self, tmp_path):
        updates = []

        def record_update(spec, downloaded, total):
            updates.append((spec, downloaded, total))

        def fake_run(*args, line_callback=None, **kwargs):
            assert line_callback is not None
            line_callback("Collecting large-pkg==1.0.0\n")
            line_callback("Progress 512 of 0\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages(
                specs=["large-pkg==1.0.0"],
                dest_dir=tmp_path,
                download_progress_callback=record_update,
            )

        assert updates == [("large-pkg==1.0.0", 512, None)]

    def test_download_stages_artifacts_from_pip_cache_wheelhouse(self, tmp_path):
        cache_root = tmp_path / "pip-cache"
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()

        def fake_run(cmd, *args, line_callback=None, **kwargs):
            del args, kwargs
            download_dir = Path(cmd[cmd.index("--dest") + 1])
            assert cache_root in download_dir.parents
            assert download_dir != stage_dir
            download_dir.mkdir(parents=True, exist_ok=True)
            wheel = download_dir / "requests-2.31.0-py3-none-any.whl"
            wheel.write_text("wheel")
            if line_callback is not None:
                line_callback(f"Saved {wheel}\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download._get_pip_cache_dir", return_value=cache_root), \
             patch("pipu_cli.download.run_pip", side_effect=fake_run):
            result = download_packages(
                specs=["requests==2.31.0"],
                dest_dir=stage_dir,
                use_download_cache=True,
            )

        staged_wheel = stage_dir / "requests-2.31.0-py3-none-any.whl"
        assert staged_wheel.exists()
        assert result == [staged_wheel]

    def test_download_stages_only_artifacts_reported_by_pip(self, tmp_path):
        cache_root = tmp_path / "pip-cache"
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()

        def fake_run(cmd, *args, line_callback=None, **kwargs):
            del args, kwargs
            download_dir = Path(cmd[cmd.index("--dest") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            stale = download_dir / "dep-1.0.0-py3-none-any.whl"
            current = download_dir / "requests-2.31.0-py3-none-any.whl"
            stale.write_text("old")
            current.write_text("current")
            if line_callback is not None:
                line_callback(f"Saved {current}\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download._get_pip_cache_dir", return_value=cache_root), \
             patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=stage_dir,
                use_download_cache=True,
            )

        assert (stage_dir / "requests-2.31.0-py3-none-any.whl").exists()
        assert not (stage_dir / "dep-1.0.0-py3-none-any.whl").exists()

    def test_download_scans_cache_dir_when_reported_artifacts_are_missing(self, tmp_path):
        cache_root = tmp_path / "pip-cache"
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()

        def fake_run(cmd, *args, line_callback=None, **kwargs):
            del args, kwargs
            download_dir = Path(cmd[cmd.index("--dest") + 1])
            download_dir.mkdir(parents=True, exist_ok=True)
            current = download_dir / "requests-2.31.0-py3-none-any.whl"
            current.write_text("current")
            if line_callback is not None:
                line_callback(f"Saved {download_dir / 'missing.whl'}\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download._get_pip_cache_dir", return_value=cache_root), \
             patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=stage_dir,
                use_download_cache=True,
            )

        assert (stage_dir / "requests-2.31.0-py3-none-any.whl").exists()

    def test_download_uses_stage_dir_when_pip_cache_is_unavailable(self, tmp_path):
        def fake_run(cmd, *args, **kwargs):
            del args, kwargs
            download_dir = Path(cmd[cmd.index("--dest") + 1])
            assert download_dir == tmp_path
            (download_dir / "requests-2.31.0-py3-none-any.whl").touch()
            return PipResult(0, "", "")

        with patch("pipu_cli.download._get_pip_cache_dir", return_value=None), \
             patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages(
                specs=["requests==2.31.0"],
                dest_dir=tmp_path,
                use_download_cache=True,
            )

    def test_download_empty_specs_returns_empty(self, tmp_path):
        result = download_packages(specs=[], dest_dir=tmp_path)
        assert result == []

    def test_download_os_error_surfaces_reason(self, tmp_path):
        """OSError from run_pip (e.g. missing interpreter) reaches the progress callback."""
        failures = []

        def fake_run(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "/nope/python")

        def callback(spec, success, error_msg):
            if not success:
                failures.append((spec, error_msg))

        with patch("pipu_cli.download.run_pip", side_effect=fake_run):
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
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

            cmd = mock_run.call_args[0][0]
            assert "--find-links" in cmd
            assert "--no-index" in cmd
            assert "--no-deps" not in cmd
            assert "requests==2.31.0" in cmd
            assert len(results) == 1
            assert results[0].upgraded is True
            assert results[0].version == Version("2.31.0")
            assert results[0].previous_version == Version("2.28.0")

    def test_install_batches_specs_so_pip_resolves_dependencies(self, tmp_path):
        pre_versions = {
            "mypy": Version("1.20.2"),
            "librt": Version("0.9.0"),
        }
        post_versions = {
            "mypy": Version("2.0.0"),
            "librt": Version("0.10.0"),
        }

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["mypy==2.0.0", "librt==0.10.0"],
            )

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--no-index" in cmd
        assert "--no-deps" not in cmd
        assert "mypy==2.0.0" in cmd
        assert "librt==0.10.0" in cmd
        assert [result.name for result in results] == ["mypy", "librt"]

    def test_install_constrains_single_version_local_artifacts(self, tmp_path):
        """The local install should not make pip search every possible version."""
        (tmp_path / "requests-2.31.0-py3-none-any.whl").touch()
        (tmp_path / "urllib3-2.0.7-py3-none-any.whl").touch()
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

        cmd = mock_run.call_args[0][0]
        assert "--constraint" in cmd
        constraints_path = Path(cmd[cmd.index("--constraint") + 1])
        assert cmd[cmd.index("--constraint") + 1] == str(constraints_path)
        assert constraints_path.parent == tmp_path
        assert constraints_path.name.startswith("pipu-local-constraints-")
        assert constraints_path.name.endswith(".txt")
        assert constraints_path.read_text() == (
            "requests==2.31.0\n"
            "urllib3==2.0.7\n"
        )

    def test_install_constraints_omit_ambiguous_local_artifact_versions(self, tmp_path):
        """Ambiguous transitive artifacts should be left for pip's resolver."""
        (tmp_path / "requests-2.31.0-py3-none-any.whl").touch()
        (tmp_path / "dep-1.0.0-py3-none-any.whl").touch()
        (tmp_path / "dep-2.0.0-py3-none-any.whl").touch()
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

        cmd = mock_run.call_args[0][0]
        constraints_path = Path(cmd[cmd.index("--constraint") + 1])
        assert constraints_path.read_text() == "requests==2.31.0\n"

    def test_install_ignores_existing_generated_constraint_files(self, tmp_path, caplog):
        """Concurrent group installs should not treat generated constraint files as artifacts."""
        (tmp_path / "pipu-local-constraints-stale.txt").write_text("stale==1\n")
        (tmp_path / "requests-2.31.0-py3-none-any.whl").touch()
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}
        caplog.set_level(logging.DEBUG, logger="pipu_cli.download")

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")), \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

        messages = "\n".join(caplog.messages)
        assert "1 pinned" in messages
        assert "0 ignored artifacts" in messages
        assert "stale" not in messages

    def test_install_debug_logs_local_constraint_diagnostics(self, tmp_path, caplog):
        """Debug logs should preserve wheelhouse constraint details after temp cleanup."""
        (tmp_path / "requests-2.31.0-py3-none-any.whl").touch()
        (tmp_path / "urllib3-2.0.7-py3-none-any.whl").touch()
        (tmp_path / "dep-1.0.0-py3-none-any.whl").touch()
        (tmp_path / "dep-2.0.0-py3-none-any.whl").touch()
        (tmp_path / "notes.txt").touch()
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}
        caplog.set_level(logging.DEBUG, logger="pipu_cli.download")

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")), \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

        messages = "\n".join(caplog.messages)
        assert "Local wheelhouse constraints:" in messages
        assert "pipu-local-constraints-" in messages
        assert "2 pinned" in messages
        assert "1 ambiguous" in messages
        assert "1 ignored artifact" in messages
        assert "dep==2.0.0" not in messages
        assert "requests==2.31.0" in messages
        assert "urllib3==2.0.7" in messages
        assert "dep: 1.0.0, 2.0.0" in messages

    def test_install_uses_idle_timeout_runner(self, tmp_path):
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_local_package_versions", side_effect=[pre_versions, post_versions]):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                timeout=900,
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["-m", "pip", "install"]
        assert mock_run.call_args.kwargs["timeout"] == 900
        assert mock_run.call_args.kwargs["timeout_mode"] == "idle"
        assert results[0].upgraded is True

    def test_install_idle_timeout_does_not_advance_packages(self, tmp_path):
        pre_versions = {"requests": Version("2.28.0")}
        callback = MagicMock()

        with patch("pipu_cli.download.run_pip", return_value=PipResult(-1, "", "", timed_out=True)), \
             patch("pipu_cli.download._get_local_package_versions", return_value=pre_versions):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                timeout=900,
                progress_callback=callback,
            )

        callback.assert_not_called()
        assert results[0].upgraded is False
        assert results[0].failure_reason == "Installation timed out after 900s without pip output"

    def test_install_timeout_returns_failed_results(self, tmp_path):
        pre_versions = {"requests": Version("2.28.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(-1, "", "", timed_out=True)), \
             patch("pipu_cli.download._get_local_package_versions", return_value=pre_versions):
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
            )

        assert len(results) == 1
        assert results[0].upgraded is False
        assert results[0].failure_reason is not None
        assert results[0].failure_reason == "Installation timed out after 300s without pip output"

    def test_install_with_python_path(self, tmp_path):
        pre_versions = {"requests": Version("2.28.0")}
        post_versions = {"requests": Version("2.31.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run, \
             patch("pipu_cli.download._get_remote_package_versions", side_effect=[pre_versions, post_versions]) as mock_remote:
            results = install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                python_path="/usr/bin/python3",
            )
            assert mock_run.call_args.kwargs["python_path"] == "/usr/bin/python3"
            assert mock_remote.call_count == 2
            assert results[0].upgraded is True

    def test_install_calls_progress_callback(self, tmp_path):
        callback = MagicMock()
        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")), \
             patch("pipu_cli.download._get_local_package_versions", return_value={}):
            install_from_local(
                dest_dir=tmp_path,
                specs=["requests==2.31.0"],
                progress_callback=callback,
            )
            callback.assert_called_once_with("requests==2.31.0")

    def test_install_failure_marks_not_upgraded(self, tmp_path):
        pre_versions = {"requests": Version("2.28.0")}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(1, "", "ERROR: install failed")), \
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
        env_plans = {
            "main": ["requests==2.31.0", "numpy==1.26.0"],
            "ml": ["requests==2.31.0", "rich==13.7.0"],
        }

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run:
            download_packages_for_group(env_plans, tmp_path)
            # requests==2.31.0 appears in both, so 3 unique specs total
            assert mock_run.call_count == 3

    def test_different_versions_both_downloaded(self, tmp_path):
        """numpy==1.25 and numpy==1.26 are different specs -- both download."""
        env_plans = {
            "main": ["numpy==1.26.0"],
            "ml": ["numpy==1.25.0"],
        }

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")) as mock_run:
            download_packages_for_group(env_plans, tmp_path)
            assert mock_run.call_count == 2

    def test_calls_progress_callback(self, tmp_path):
        callback = MagicMock()
        env_plans = {"main": ["requests==2.31.0"]}

        with patch("pipu_cli.download.run_pip", return_value=PipResult(0, "", "")):
            download_packages_for_group(env_plans, tmp_path, progress_callback=callback)
            callback.assert_called_once_with("requests==2.31.0", True, "")

    def test_forwards_raw_download_progress_callback(self, tmp_path):
        updates = []
        env_plans = {"main": ["requests==2.31.0"]}

        def record_update(spec, downloaded, total):
            updates.append((spec, downloaded, total))

        def fake_run(*args, line_callback=None, **kwargs):
            assert line_callback is not None
            line_callback("Progress 10 of 20\n")
            return PipResult(0, "", "")

        with patch("pipu_cli.download.run_pip", side_effect=fake_run):
            download_packages_for_group(
                env_plans,
                tmp_path,
                download_progress_callback=record_update,
            )

        assert updates == [("requests==2.31.0", 10, 20)]

    def test_empty_plans_returns_empty(self, tmp_path):
        result = download_packages_for_group({}, tmp_path)
        assert result == []
