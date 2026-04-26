"""Fix-plan primitives for ``pipu check --fix``.

This module is pure: all external side effects (subprocess, filesystem
writes) are injected by callers so the logic can be unit-tested
without I/O.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from packaging.utils import NormalizedName, canonicalize_name

from pipu_cli.package_management import DepProblem, EnvReport, InstalledResult


FIXABLE_KINDS = ("stale-metadata", "violates")


@dataclass(frozen=True)
class FixResult:
    """Outcome of one attempted fix.

    :param problem: The :class:`DepProblem` the fix targeted.
    :param action: ``"delete"`` (stale-metadata) or ``"install"``
        (violates).
    :param target: Filesystem path for deletes, pip spec string for
        installs.
    :param status: ``"succeeded"`` / ``"failed"`` / ``"skipped"`` /
        ``"unfixable"``.
    :param detail: Error message or skip reason; ``None`` on success.
    """

    problem: DepProblem
    action: str
    target: str
    status: str
    detail: Optional[str]


@dataclass
class FixPlan:
    """Ordered batch of fixes for a single env.

    :param python_path: Env identity; ``None`` for local.
    :param stale_metadata: Problems to delete, sorted alphabetically
        by package name.
    :param violates: Problems to install, sorted alphabetically.
    :param unfixable: Problems whose kinds have no fix handler
        (missing / broken-editable / duplicate-install), retained so
        the summary can report them.
    """

    python_path: Optional[str]
    stale_metadata: List[DepProblem]
    violates: List[DepProblem]
    unfixable: List[DepProblem]


def build_fix_plan(report: EnvReport) -> FixPlan:
    """Partition an :class:`EnvReport` into fixable / unfixable buckets.

    :param report: The report to plan against.
    :returns: A :class:`FixPlan` with each bucket sorted alphabetically
        by ``DepProblem.package``.
    """
    stale: List[DepProblem] = []
    violates: List[DepProblem] = []
    unfixable: List[DepProblem] = []
    for p in report.problems:
        if p.kind == "stale-metadata":
            stale.append(p)
        elif p.kind == "violates":
            violates.append(p)
        else:
            unfixable.append(p)
    stale.sort(key=lambda p: p.package)
    violates.sort(key=lambda p: p.package)
    return FixPlan(
        python_path=report.python_path,
        stale_metadata=stale,
        violates=violates,
        unfixable=unfixable,
    )


_ORPHAN_SUFFIXES = (".egg-info", ".dist-info")


def _extract_orphan_path(detail: str) -> Optional[str]:
    """Pull the orphan path out of a ``stale-metadata`` detail string.

    Detail format is
    ``"{package} has orphaned metadata: {path1}, {path2}..."`` (produced by
    :func:`pipu_cli.package_management._collect_problems_over_edges`).
    Only the first path is returned; callers fix one path at a time.

    :param detail: The :class:`DepProblem.detail` string.
    :returns: First path in the list, or ``None`` when the marker is
        missing.
    """
    marker = "has orphaned metadata: "
    idx = detail.find(marker)
    if idx == -1:
        return None
    tail = detail[idx + len(marker):]
    return tail.split(", ", 1)[0].strip()


def apply_stale_metadata_fix(
    problem: DepProblem,
    *,
    python_path: Optional[str],
    verifier: Callable[[Optional[str]], Dict[str, List[Dict[str, str]]]],
    remover: Callable[[str], None],
) -> FixResult:
    """Re-verify orphan status, then delete the metadata directory.

    :param problem: A :class:`DepProblem` with ``kind == "stale-metadata"``.
    :param python_path: Env identity; forwarded to ``verifier``.
    :param verifier: Callable returning the current orphan map
        (canonical-name to list of ``{"version", "path"}`` dicts).
        Typically :func:`pipu_cli.package_management.get_orphan_metadata`.
    :param remover: Callable removing a path (e.g., ``shutil.rmtree``
        for directories). Injected for testability.
    :returns: A :class:`FixResult` describing the outcome.
    """
    path = _extract_orphan_path(problem.detail)
    if path is None:
        return FixResult(
            problem=problem, action="delete", target="",
            status="skipped",
            detail="unable to parse orphan path from problem detail",
        )

    if not path.endswith(_ORPHAN_SUFFIXES):
        return FixResult(
            problem=problem, action="delete", target=path,
            status="skipped",
            detail=f"path does not end in {_ORPHAN_SUFFIXES}",
        )

    current = verifier(python_path)
    entries = current.get(problem.package, [])
    if not any(entry.get("path") == path for entry in entries):
        return FixResult(
            problem=problem, action="delete", target=path,
            status="skipped",
            detail="path is no longer classified as orphan",
        )

    try:
        remover(path)
    except Exception as exc:
        return FixResult(
            problem=problem, action="delete", target=path,
            status="failed", detail=str(exc),
        )
    return FixResult(
        problem=problem, action="delete", target=path,
        status="succeeded", detail=None,
    )


def apply_violates_fix(
    problems: List[DepProblem],
    *,
    python_path: Optional[str],
    installer: Callable[..., List[InstalledResult]],
) -> List[FixResult]:
    """Resolve a batch of ``violates`` problems with one pip install per package.

    Problems that target the same canonical package are merged into a
    single spec (comma-joined specifiers) and one ``installer`` call.
    Each input problem yields its own :class:`FixResult`; problems
    sharing a package share the same ``status`` / ``detail`` / ``target``
    derived from the pip outcome for that package.

    :param problems: Problems with ``kind == "violates"``.
    :param python_path: Env identity; forwarded to ``installer``.
    :param installer: Callable matching
        :func:`pipu_cli.package_management.run_pip_install`. Called as
        ``installer(package_specs=..., upgrade=True, timeout=300,
        python_path=...)``.
    :returns: One :class:`FixResult` per input problem, in the input
        order.
    """
    if not problems:
        return []

    groups: Dict[NormalizedName, List[DepProblem]] = defaultdict(list)
    order: List[NormalizedName] = []
    for p in problems:
        key = canonicalize_name(p.package)
        if key not in groups:
            order.append(key)
        groups[key].append(p)

    outcomes: Dict[NormalizedName, FixResult] = {}
    for key in order:
        group = groups[key]
        merged_spec = (
            f"{group[0].package}"
            f"{','.join(p.specifier or '' for p in group)}"
        )
        try:
            results = installer(
                package_specs=[merged_spec],
                upgrade=True, timeout=300, python_path=python_path,
            )
        except Exception as exc:
            outcomes[key] = FixResult(
                problem=group[0], action="install", target=merged_spec,
                status="failed", detail=str(exc),
            )
            continue

        if results and results[0].installed:
            outcomes[key] = FixResult(
                problem=group[0], action="install", target=merged_spec,
                status="succeeded", detail=None,
            )
        else:
            reason = results[0].failure_reason if results else "no result returned"
            outcomes[key] = FixResult(
                problem=group[0], action="install", target=merged_spec,
                status="failed", detail=reason,
            )

    return [
        FixResult(
            problem=p,
            action=outcomes[canonicalize_name(p.package)].action,
            target=outcomes[canonicalize_name(p.package)].target,
            status=outcomes[canonicalize_name(p.package)].status,
            detail=outcomes[canonicalize_name(p.package)].detail,
        )
        for p in problems
    ]
