"""Download-then-install pipeline for pipu upgrade."""

import hashlib
import logging
import os
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from pipu_cli._subprocess import run_pip
from pipu_cli.package_management import (
    CONSTRAINED_BY_RESOLVER_REASON,
    UpgradedPackage,
    _get_local_package_versions,
    _get_remote_package_versions,
    parse_package_spec,
)


def _canonical_name_for_spec(spec: str) -> str:
    """Return a canonical key for ``spec``, tolerating VCS/URL inputs.

    ``parse_package_spec`` raises :class:`ValueError` for inputs that
    aren't valid PEP 508 requirements or local files (e.g. ``git+https://``
    URLs). Such specs are still valid pip arguments, so we fall back to
    :func:`packaging.utils.canonicalize_name` on the raw spec to produce a
    stable lookup key without aborting the batch.
    """
    try:
        return parse_package_spec(spec).name
    except ValueError:
        return canonicalize_name(spec)

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 300
LOCAL_CONSTRAINTS_FILENAME = "pipu-local-constraints.txt"
LOCAL_CONSTRAINTS_PREFIX = "pipu-local-constraints-"
RAW_PROGRESS_RE = re.compile(r"^Progress\s+(\d+)\s+of\s+(\d+)\s*$")
SAVED_ARTIFACT_RE = re.compile(r"^(?:Saved|File was already downloaded)\s+(.+?)\s*$")
PIPU_DOWNLOAD_CACHE_DIR = "pipu-downloads"


def download_watchdog_timeout(timeout: int) -> int:
    """Return the no-output watchdog timeout for pip download subprocesses.

    ``timeout`` is passed through to pip as its network/socket timeout. The
    outer watchdog needs a larger floor because pip can legitimately spend
    more than a few seconds resolving, consulting caches, or waiting on a slow
    proxy before it emits byte-level progress.

    :param timeout: User-configured pip network timeout.
    :returns: Subprocess idle timeout used by pipu.
    """
    return max(timeout, DOWNLOAD_TIMEOUT)


class DownloadError(RuntimeError):
    """Raised when one or more package downloads fail.

    :param failed: Mapping of package spec to the diagnostic emitted by pip.
    """

    def __init__(self, failed: Dict[str, str]) -> None:
        self.failed = dict(failed)
        super().__init__(f"Failed to download: {', '.join(self.failed)}")


@dataclass(frozen=True)
class _LocalConstraintDiagnostics:
    """Details worth preserving from the temporary local wheelhouse."""

    path: Optional[Path]
    pins: List[str]
    ambiguous: Dict[str, List[Version]]
    ignored_artifacts: int


def _parse_raw_progress(line: str) -> Optional[Tuple[int, Optional[int]]]:
    """Parse pip's ``--progress-bar raw`` output.

    :param line: Raw stdout/stderr line from pip.
    :returns: ``(downloaded_bytes, total_bytes)`` or ``None`` when the line is
        not raw download progress. A total of ``0`` means pip does not know the
        final size.
    """
    match = RAW_PROGRESS_RE.match(line.strip())
    if not match:
        return None
    downloaded = int(match.group(1))
    total = int(match.group(2))
    return downloaded, total if total > 0 else None


def _is_resolution_too_deep_error(reason: str) -> bool:
    """Return True when pip reports an over-deep dependency graph."""
    lower_reason = reason.lower()
    return (
        "resolution-too-deep" in lower_reason
        or "dependency resolution exceeded maximum depth" in lower_reason
    )


def _is_dependency_resolution_conflict(reason: str) -> bool:
    """Return True when pip reports an unsatisfiable dependency graph."""
    lower_reason = reason.lower()
    return (
        "resolutionimpossible" in lower_reason
        or "conflicting dependencies" in lower_reason
        or "dealing-with-dependency-conflicts" in lower_reason
    )


def _is_retryable_resolver_error(reason: str) -> bool:
    """Return True when a smaller install request may isolate pip resolver failure."""
    return (
        _is_resolution_too_deep_error(reason)
        or _is_dependency_resolution_conflict(reason)
    )


def _download_activity_status(line: str) -> Optional[str]:
    """Return a compact status for pip download work that is not resolver chatter."""
    if _parse_raw_progress(line) is not None:
        return "receiving data"

    status = line.strip()
    build_statuses = (
        ("Installing build dependencies", "installing build dependencies"),
        ("Getting requirements to build", "getting build requirements"),
        ("Installing backend dependencies", "installing backend dependencies"),
        ("Preparing metadata", "preparing metadata"),
        ("Preparing wheel metadata", "preparing metadata"),
        ("Building wheel", "building wheel"),
        ("Building wheels for collected packages", "building wheels"),
    )
    for prefix, label in build_statuses:
        if status.startswith(prefix):
            return label
    return None


def _parse_saved_artifact(line: str, download_dir: Path) -> Optional[Path]:
    """Return the artifact path pip reported saving into ``download_dir``."""
    match = SAVED_ARTIFACT_RE.match(line.strip())
    if not match:
        return None
    path = Path(match.group(1)).expanduser()
    if not path.is_absolute():
        path = download_dir / path
    try:
        path.relative_to(download_dir)
    except ValueError:
        return None
    return path


def _artifact_name_version(path: Path) -> Optional[tuple[str, Version]]:
    """Return the normalized name/version encoded in a local artifact filename."""
    try:
        if path.name.endswith(".whl"):
            name, version, _build, _tags = parse_wheel_filename(path.name)
        else:
            name, version = parse_sdist_filename(path.name)
    except (InvalidSdistFilename, InvalidWheelFilename):
        return None
    return canonicalize_name(name), version


def _is_generated_constraints_file(path: Path) -> bool:
    """Return True for constraint files created by this module."""
    return (
        path.name == LOCAL_CONSTRAINTS_FILENAME
        or (path.name.startswith(LOCAL_CONSTRAINTS_PREFIX) and path.name.endswith(".txt"))
    )


def _local_constraints_path(dest_dir: Path) -> Path:
    """Build a per-thread constraints path for concurrent group installs."""
    return dest_dir / f"{LOCAL_CONSTRAINTS_PREFIX}{threading.get_ident()}.txt"


def _get_pip_cache_dir(executable: str) -> Optional[Path]:
    """Return pip's cache root for ``executable`` when it can be resolved."""
    try:
        result = run_pip(
            ["-m", "pip", "cache", "dir"],
            python_path=executable,
            timeout=30,
            stream_output=False,
        )
    except OSError as exc:
        logger.debug("Could not resolve pip cache directory: %s", exc)
        return None

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"pip exit code {result.returncode}"
        logger.debug("Could not resolve pip cache directory: %s", msg)
        return None

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        logger.debug("Could not resolve pip cache directory: pip returned no path")
        return None
    return Path(lines[-1]).expanduser()


def _download_cache_dir(cache_root: Path, executable: str, spec: str, pre: bool) -> Path:
    """Return a stable cache wheelhouse for one pip download request."""
    key = f"{executable}\0{spec}\0pre={pre}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "-", spec).strip("-_.")[:80]
    name = readable or "download"
    return cache_root / PIPU_DOWNLOAD_CACHE_DIR / f"{name}-{digest}"


def _link_or_copy_artifact(source: Path, target: Path) -> None:
    """Stage ``source`` into ``target`` without duplicating bytes when possible."""
    if source == target or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        return
    except OSError:
        shutil.copy2(source, target)


def _stage_download_artifacts(
    download_dir: Path,
    stage_dir: Path,
    reported_artifacts: list[Path],
) -> None:
    """Copy or hardlink current download artifacts into the per-run wheelhouse."""
    if download_dir == stage_dir:
        return

    def cache_artifacts() -> list[Path]:
        return [
            path for path in download_dir.iterdir()
            if path.is_file() and _artifact_name_version(path) is not None
        ]

    if reported_artifacts:
        candidates = list(dict.fromkeys(reported_artifacts))
    else:
        candidates = cache_artifacts()

    staged = 0
    for source in candidates:
        if not source.is_file() or _artifact_name_version(source) is None:
            continue
        _link_or_copy_artifact(source, stage_dir / source.name)
        staged += 1

    if staged == 0 and reported_artifacts:
        logger.debug(
            "No reported artifacts from %s could be staged; scanning cache directory",
            download_dir,
        )
        for source in cache_artifacts():
            _link_or_copy_artifact(source, stage_dir / source.name)
            staged += 1

    logger.debug(
        "Staged %d download artifact(s) from %s into %s",
        staged,
        download_dir,
        stage_dir,
    )


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _log_local_constraint_diagnostics(diagnostics: _LocalConstraintDiagnostics) -> None:
    """Emit wheelhouse constraint details before the temporary directory is removed."""
    constraint_target = str(diagnostics.path) if diagnostics.path is not None else "not generated"
    logger.debug(
        "Local wheelhouse constraints: %s (%d pinned, %d ambiguous, %d ignored %s)",
        constraint_target,
        len(diagnostics.pins),
        len(diagnostics.ambiguous),
        diagnostics.ignored_artifacts,
        _plural(diagnostics.ignored_artifacts, "artifact", "artifacts"),
    )
    if diagnostics.pins:
        logger.debug(
            "Local wheelhouse constraint pins:\n  %s",
            "\n  ".join(diagnostics.pins),
        )
    if diagnostics.ambiguous:
        ambiguous_lines = [
            f"{name}: {', '.join(str(version) for version in versions)}"
            for name, versions in diagnostics.ambiguous.items()
        ]
        logger.debug(
            "Local wheelhouse ambiguous artifacts:\n  %s",
            "\n  ".join(ambiguous_lines),
        )


def _exact_spec_version(spec: str) -> Optional[tuple[str, Version]]:
    """Return the exact version requested by ``spec`` when it has one."""
    try:
        parsed = parse_package_spec(spec)
    except ValueError:
        return None

    specifiers = list(parsed.specifier)
    if len(specifiers) != 1:
        return None
    specifier = specifiers[0]
    if specifier.operator not in {"==", "==="} or "*" in specifier.version:
        return None
    try:
        return parsed.name, Version(specifier.version)
    except InvalidVersion:
        return None


def _normalize_version_mapping(
    versions: Optional[Mapping[str, Version]],
) -> Dict[str, Version]:
    """Return ``versions`` keyed by canonical package name."""
    if versions is None:
        return {}
    return {
        canonicalize_name(name): version
        for name, version in versions.items()
    }


def _write_local_constraints(
    dest_dir: Path,
    specs: Optional[List[str]] = None,
    installed_versions: Optional[Mapping[str, Version]] = None,
    planned_versions: Optional[Mapping[str, Version]] = None,
) -> Optional[Path]:
    """Write resolver constraints for the current offline install plan."""
    versions_by_name: Dict[str, set[Version]] = {}
    ignored_artifacts = 0
    for path in dest_dir.iterdir():
        if not path.is_file():
            continue
        if _is_generated_constraints_file(path):
            continue
        parsed = _artifact_name_version(path)
        if parsed is None:
            ignored_artifacts += 1
            continue
        name, version = parsed
        versions_by_name.setdefault(name, set()).add(version)

    target_names = {
        _canonical_name_for_spec(spec)
        for spec in specs or []
    }
    normalized_planned_versions = _normalize_version_mapping(planned_versions)
    normalized_installed_versions = _normalize_version_mapping(installed_versions)

    pins: Dict[str, Version] = {}
    for spec in specs or []:
        name = _canonical_name_for_spec(spec)
        planned_version = normalized_planned_versions.get(name)
        exact = _exact_spec_version(spec)
        artifact_versions = versions_by_name.get(name, set())
        if planned_version is not None:
            pins[name] = planned_version
        elif exact is not None and exact[0] == name:
            pins[name] = exact[1]
        elif len(artifact_versions) == 1:
            pins[name] = next(iter(artifact_versions))

    for name, version in normalized_installed_versions.items():
        if name not in target_names:
            # Pip may otherwise upgrade installed dependencies that were not in
            # pipu's preview, then only warn about conflicts after changing the env.
            pins[name] = version

    lines = [
        f"{name}=={version}"
        for name, version in sorted(pins.items())
    ]
    ambiguous: Dict[str, List[Version]] = {
        name: sorted(versions)
        for name, versions in sorted(versions_by_name.items())
        if len(versions) != 1 and name not in pins
    }

    if not lines:
        _log_local_constraint_diagnostics(
            _LocalConstraintDiagnostics(
                path=None,
                pins=[],
                ambiguous=ambiguous,
                ignored_artifacts=ignored_artifacts,
            )
        )
        return None

    constraints_path = _local_constraints_path(dest_dir)
    constraints_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log_local_constraint_diagnostics(
        _LocalConstraintDiagnostics(
            path=constraints_path,
            pins=lines,
            ambiguous=ambiguous,
            ignored_artifacts=ignored_artifacts,
        )
    )
    return constraints_path


def _download_single(
    spec: str,
    download_dir: Path,
    stage_dir: Path,
    executable: str,
    pre: bool,
    timeout: int,
    download_progress_callback: Optional[Callable[[str, int, Optional[int]], None]] = None,
    download_activity_callback: Optional[Callable[[str, str], None]] = None,
) -> Tuple[str, bool, str]:
    """Download a single package. Returns (spec, success, error_message)."""
    # Include dependencies here so the later install phase can run local-only;
    # otherwise pip can surprise users with downloads after the download bar.
    watchdog_timeout = download_watchdog_timeout(timeout)
    cmd = [
        "-m",
        "pip",
        "download",
        "--dest",
        str(download_dir),
        "--timeout",
        str(timeout),
        "--progress-bar",
        "raw",
    ]
    if pre:
        cmd.append("--pre")
    cmd.append(spec)

    logger.debug(f"Running: {executable} {' '.join(cmd)}")
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        reported_artifacts: list[Path] = []

        def on_line(line: str) -> None:
            saved_artifact = _parse_saved_artifact(line, download_dir)
            if saved_artifact is not None:
                reported_artifacts.append(saved_artifact)
            parsed = _parse_raw_progress(line)
            if parsed is not None:
                downloaded, total = parsed
                if download_progress_callback is not None:
                    download_progress_callback(spec, downloaded, total)
                return
            status = _download_activity_status(line)
            if status is not None and download_activity_callback is not None:
                download_activity_callback(spec, status)

        result = run_pip(
            cmd,
            python_path=executable,
            timeout=watchdog_timeout,
            stream_output=False,
            timeout_mode="idle",
            line_callback=on_line,
        )
        if result.timed_out:
            logger.warning(
                f"Download timed out for {spec} after {watchdog_timeout}s without pip output"
            )
            return (spec, False, f"timed out after {watchdog_timeout}s without pip output")
        if result.returncode != 0:
            msg = result.stderr.strip() or f"pip exit code {result.returncode}"
            logger.warning(f"Failed to download {spec}: {msg}")
            return (spec, False, msg)
        _stage_download_artifacts(download_dir, stage_dir, reported_artifacts)
        return (spec, True, "")
    except OSError as e:
        # e.g. missing interpreter (FileNotFoundError), permission denied.
        # Surface via the result tuple so progress callbacks / trackers see it
        # instead of a raw traceback killing the thread pool.
        logger.warning(f"OS error downloading {spec}: {e}")
        return (spec, False, f"OS error: {e}")


def download_packages(
    specs: List[str],
    dest_dir: Path,
    python_path: Optional[str] = None,
    pre: bool = False,
    timeout: int = DOWNLOAD_TIMEOUT,
    max_workers: int = 1,
    progress_callback: Optional[Callable[[str, bool, str], None]] = None,
    start_callback: Optional[Callable[[str], None]] = None,
    download_progress_callback: Optional[Callable[[str, int, Optional[int]], None]] = None,
    download_activity_callback: Optional[Callable[[str, str], None]] = None,
    use_download_cache: bool = False,
) -> List[Path]:
    """Download packages to a local directory using pip download.

    :param specs: Version-pinned package specs (e.g., ["requests==2.31.0"])
    :param dest_dir: Directory to download wheels/sdists into
    :param python_path: Python interpreter path (default: current Python)
    :param pre: Include pre-release versions
    :param timeout: Pip network timeout in seconds. The subprocess no-output
        watchdog keeps a larger floor via :func:`download_watchdog_timeout`.
    :param max_workers: Number of parallel downloads
    :param progress_callback: Called with (spec, success, error_msg) after each download
    :param start_callback: Called with (spec) when a download begins
    :param download_progress_callback: Called with (spec, downloaded, total)
        for pip raw progress lines. ``total`` is ``None`` when pip reports an
        unknown size.
    :param download_activity_callback: Called with (spec, status) for pip build
        or metadata work that should keep the download watchdog alive without
        being presented as dependency-resolution progress.
    :param use_download_cache: Store downloaded artifacts under pip's cache
        root and stage them into ``dest_dir`` for the install phase.
    :returns: List of paths to downloaded files
    :raises RuntimeError: If any downloads fail
    """
    if not specs:
        return []

    executable = python_path or sys.executable
    failed: Dict[str, str] = {}
    cache_root = _get_pip_cache_dir(executable) if use_download_cache else None

    def download_dir_for(spec: str) -> Path:
        if cache_root is None:
            return dest_dir
        return _download_cache_dir(cache_root, executable, spec, pre)

    if max_workers > 1:
        def _download_with_start(spec: str) -> Tuple[str, bool, str]:
            if start_callback:
                start_callback(spec)
            return _download_single(
                spec,
                download_dir_for(spec),
                dest_dir,
                executable,
                pre,
                timeout,
                download_progress_callback,
                download_activity_callback,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_download_with_start, spec): spec for spec in specs}
            for future in as_completed(futures):
                spec, success, error_msg = future.result()
                if not success:
                    failed[spec] = error_msg or "download failed"
                if progress_callback:
                    progress_callback(spec, success, error_msg)
    else:
        for spec in specs:
            if start_callback:
                start_callback(spec)
            spec, success, error_msg = _download_single(
                spec,
                download_dir_for(spec),
                dest_dir,
                executable,
                pre,
                timeout,
                download_progress_callback,
                download_activity_callback,
            )
            if not success:
                failed[spec] = error_msg or "download failed"
            if progress_callback:
                progress_callback(spec, success, error_msg)

    if failed:
        raise DownloadError(failed)

    return list(dest_dir.iterdir())


def download_packages_for_group(
    env_upgrade_plans: Dict[str, List[str]],
    dest_dir: Path,
    pre: bool = False,
    timeout: int = DOWNLOAD_TIMEOUT,
    max_workers: int = 1,
    progress_callback: Optional[Callable[[str, bool, str], None]] = None,
    start_callback: Optional[Callable[[str], None]] = None,
    download_progress_callback: Optional[Callable[[str, int, Optional[int]], None]] = None,
    download_activity_callback: Optional[Callable[[str, str], None]] = None,
    use_download_cache: bool = False,
) -> List[Path]:
    """Download deduplicated packages for a group of environments.

    :param env_upgrade_plans: Dict mapping env short names to lists of pinned specs
    :param dest_dir: Shared directory to download into
    :param pre: Include pre-release versions
    :param timeout: Pip network timeout in seconds. The subprocess no-output
        watchdog keeps a larger floor via :func:`download_watchdog_timeout`.
    :param max_workers: Number of parallel downloads
    :param progress_callback: Called with (spec, success, error_msg) after each download
    :param start_callback: Called with (spec) when a download begins
    :param download_progress_callback: Called with (spec, downloaded, total)
        for pip raw progress lines. ``total`` is ``None`` when pip reports an
        unknown size.
    :param download_activity_callback: Called with (spec, status) for pip build
        or metadata work that should keep the download watchdog alive without
        being presented as dependency-resolution progress.
    :param use_download_cache: Store downloaded artifacts under pip's cache
        root and stage them into ``dest_dir`` for the install phase.
    :returns: List of paths to downloaded files
    """
    if not env_upgrade_plans:
        return []

    unique_specs: List[str] = list(dict.fromkeys(
        spec for specs in env_upgrade_plans.values() for spec in specs
    ))

    return download_packages(
        specs=unique_specs,
        dest_dir=dest_dir,
        pre=pre,
        timeout=timeout,
        max_workers=max_workers,
        progress_callback=progress_callback,
        start_callback=start_callback,
        download_progress_callback=download_progress_callback,
        download_activity_callback=download_activity_callback,
        use_download_cache=use_download_cache,
    )


def install_from_local(
    dest_dir: Path,
    specs: List[str],
    python_path: Optional[str] = None,
    timeout: int = DOWNLOAD_TIMEOUT,
    progress_callback: Optional[Callable[[str], None]] = None,
    install_activity_callback: Optional[Callable[[str], None]] = None,
    installed_versions: Optional[Mapping[str, Version]] = None,
    planned_versions: Optional[Mapping[str, Version]] = None,
) -> List[UpgradedPackage]:
    """Install packages from a local directory.

    :param dest_dir: Directory containing downloaded wheels/sdists
    :param specs: Version-pinned package specs to install
    :param python_path: Python interpreter path (default: current Python)
    :param timeout: Subprocess timeout in seconds
    :param progress_callback: Called with package spec after each install completes
    :param install_activity_callback: Called with raw pip output lines while
        the install subprocess is active.
    :param installed_versions: Versions present before the upgrade, keyed by
        package name. Non-target packages are pinned to these versions so pip
        cannot silently upgrade dependencies outside pipu's resolved plan.
    :param planned_versions: Exact target versions chosen by pipu's planner,
        keyed by package name.
    :returns: List of UpgradedPackage results
    """
    if not specs:
        return []

    executable = python_path or sys.executable
    canonical_names = [_canonical_name_for_spec(s) for s in specs]

    if python_path:
        pre_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        pre_versions = _get_local_package_versions(canonical_names)

    # Keep install offline so the preceding download phase owns all network I/O.
    constraints_path = _write_local_constraints(
        dest_dir,
        specs=specs,
        installed_versions=installed_versions,
        planned_versions=planned_versions,
    )
    normalized_planned_versions = _normalize_version_mapping(planned_versions)

    def build_install_command(
        command_specs: List[str],
        *,
        use_constraints: bool = True,
    ) -> List[str]:
        cmd = [
            "-m", "pip", "install", "--upgrade",
            "--no-index",
            "--find-links", str(dest_dir),
        ]
        if use_constraints and constraints_path is not None:
            cmd.extend(["--constraint", str(constraints_path)])
        cmd.extend(command_specs)
        return cmd

    def failed_results(reason: str) -> List[UpgradedPackage]:
        return [
            UpgradedPackage(
                name=_canonical_name_for_spec(spec),
                version=pre_versions.get(_canonical_name_for_spec(spec), Version("0")),
                upgraded=False,
                previous_version=pre_versions.get(_canonical_name_for_spec(spec), Version("0")),
                failure_reason=reason,
            )
            for spec in specs
        ]

    def install_failure_reason(result) -> str:
        error_output = result.stderr.strip() or result.stdout.strip()
        return error_output or f"pip exit code {result.returncode}"

    def exact_retry_spec(spec: str) -> str:
        try:
            parsed = parse_package_spec(spec)
        except ValueError:
            return spec
        if parsed.specifier:
            return spec
        planned_version = normalized_planned_versions.get(parsed.name)
        if planned_version is None:
            return spec
        return f"{spec}=={planned_version}"

    def run_local_install(
        command_specs: List[str],
        *,
        use_constraints: bool = True,
    ) -> Tuple[Optional[str], bool]:
        cmd = build_install_command(command_specs, use_constraints=use_constraints)
        logger.debug(f"Running: {executable} {' '.join(cmd)}")
        try:
            result = run_pip(
                cmd,
                python_path=executable,
                timeout=timeout,
                stream_output=False,
                timeout_mode="idle",
                line_callback=install_activity_callback,
            )
        except OSError as e:
            return f"Installation failed: {e}", False

        if result.timed_out:
            return f"Installation timed out after {timeout}s without pip output", True

        if result.returncode != 0:
            return install_failure_reason(result), False

        return None, False

    reason, timed_out = run_local_install(specs)
    if timed_out:
        return failed_results(reason or f"Installation timed out after {timeout}s without pip output")

    failed_specs: Dict[str, str] = {}

    if reason is None:
        for spec in specs:
            if progress_callback:
                progress_callback(spec)
    elif _is_retryable_resolver_error(reason):
        logger.debug(
            "Local install hit pip resolver failure; "
            "retrying %d specs individually",
            len(specs),
        )
        for spec in specs:
            spec_reason, spec_timed_out = run_local_install(
                [exact_retry_spec(spec)],
                use_constraints=False,
            )
            if spec_reason is not None:
                failed_specs[spec] = spec_reason
            if progress_callback and not spec_timed_out:
                progress_callback(spec)
    else:
        failed_specs = {spec: reason for spec in specs}
        for spec in specs:
            if progress_callback:
                progress_callback(spec)

    if python_path:
        post_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        post_versions = _get_local_package_versions(canonical_names)

    results: List[UpgradedPackage] = []
    for spec in specs:
        name = _canonical_name_for_spec(spec)
        pre_ver = pre_versions.get(name)
        post_ver = post_versions.get(name)

        if spec in failed_specs:
            results.append(UpgradedPackage(
                name=name,
                version=pre_ver or Version("0"),
                upgraded=False,
                previous_version=pre_ver or Version("0"),
                failure_reason=failed_specs[spec],
            ))
        elif post_ver and pre_ver and post_ver > pre_ver:
            results.append(UpgradedPackage(
                name=name,
                version=post_ver,
                upgraded=True,
                previous_version=pre_ver,
            ))
        elif post_ver:
            upgraded = post_ver != pre_ver if pre_ver else True
            results.append(UpgradedPackage(
                name=name,
                version=post_ver,
                upgraded=upgraded,
                previous_version=pre_ver or post_ver,
                failure_reason=None if upgraded else CONSTRAINED_BY_RESOLVER_REASON,
            ))
        else:
            results.append(UpgradedPackage(
                name=name,
                version=pre_ver or Version("0"),
                upgraded=False,
                previous_version=pre_ver or Version("0"),
                failure_reason="Package not found after install",
            ))

    return results
