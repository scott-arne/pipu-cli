"""Fix-plan primitives for ``pipu check --fix``.

This module is pure: all external side effects (subprocess, filesystem
writes) are injected by callers so the logic can be unit-tested
without I/O.
"""

from dataclasses import dataclass
from typing import List, Optional

from pipu_cli.package_management import DepProblem, EnvReport


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
