"""Tests for the _fix_cli module."""

from io import StringIO

from packaging.version import Version
from rich.console import Console

from pipu_cli._fix_cli import Prompter, render_fix_line, render_fix_summary, run_fix
from pipu_cli.fixer import FixResult
from pipu_cli.package_management import DepProblem, EnvReport, InstalledResult


def _console_with_buf():
    buf = StringIO()
    return (
        Console(file=buf, force_terminal=False, width=120, color_system=None),
        buf,
    )


def _stale(package="foo", path="/x") -> FixResult:
    return FixResult(
        problem=DepProblem(kind="stale-metadata", package=package,
                           detail=f"{package} has orphaned metadata: {path}"),
        action="delete", target=path, status="succeeded", detail=None,
    )


def _violates(package="urllib3", spec="<2", prev="2.2.2") -> FixResult:
    return FixResult(
        problem=DepProblem(kind="violates", package=package,
                           detail=f"{package} {prev} violates httpx{spec}",
                           required_by="httpx", specifier=spec,
                           installed_version=Version(prev)),
        action="install", target=f"{package}{spec}",
        status="succeeded", detail=None,
    )


def test_render_fix_line_succeeded_delete():
    console, buf = _console_with_buf()
    render_fix_line(console, _stale(path="/x.egg-info"))
    out = buf.getvalue()
    assert "deleted" in out
    assert "/x.egg-info" in out


def test_render_fix_line_succeeded_install_shows_was():
    console, buf = _console_with_buf()
    render_fix_line(console, _violates())
    out = buf.getvalue()
    assert "installed" in out
    assert "urllib3<2" in out
    assert "was 2.2.2" in out


def test_render_fix_line_failed_includes_detail():
    console, buf = _console_with_buf()
    fix = FixResult(
        problem=DepProblem(kind="violates", package="numpy",
                           detail="numpy 2.0 violates scipy<2",
                           required_by="scipy", specifier="<2",
                           installed_version=Version("2.0")),
        action="install", target="numpy<2", status="failed",
        detail="ResolutionImpossible: needed numpy>=2",
    )
    render_fix_line(console, fix)
    out = buf.getvalue()
    assert "failed" in out
    assert "ResolutionImpossible" in out


def test_render_fix_line_skipped():
    console, buf = _console_with_buf()
    fix = FixResult(
        problem=DepProblem(kind="stale-metadata", package="foo",
                           detail="foo has orphaned metadata: /x"),
        action="delete", target="/x", status="skipped",
        detail="user declined",
    )
    render_fix_line(console, fix)
    assert "skipped" in buf.getvalue()


def test_render_fix_summary_shows_counts_and_unfixable():
    console, buf = _console_with_buf()
    fixes = [
        _stale(package="a"),
        _stale(package="b"),
        _violates(package="urllib3"),
        FixResult(
            problem=DepProblem(kind="missing", package="pandas",
                               detail="pandas missing"),
            action="install", target="", status="unfixable", detail=None,
        ),
    ]
    render_fix_summary(console, fixes)
    out = buf.getvalue()
    assert "3 applied" in out
    assert "0 failed" in out
    assert "1 unfixable" in out
    assert "missing" in out


def test_prompter_disabled_always_approves():
    p = Prompter(interactive=False, prompt_fn=lambda msg: "n")
    assert p.should_apply(kind="stale-metadata", message="fix this?") is True
    assert p.should_apply(kind="violates", message="fix that?") is True
    assert p.should_quit is False


def test_prompter_yes():
    p = Prompter(interactive=True, prompt_fn=lambda msg: "y")
    assert p.should_apply(kind="stale-metadata", message="fix?") is True
    assert p.should_quit is False


def test_prompter_no():
    p = Prompter(interactive=True, prompt_fn=lambda msg: "n")
    assert p.should_apply(kind="stale-metadata", message="fix?") is False
    assert p.should_quit is False


def test_prompter_default_on_enter_is_no():
    p = Prompter(interactive=True, prompt_fn=lambda msg: "")
    assert p.should_apply(kind="stale-metadata", message="fix?") is False


def test_prompter_a_is_sticky_within_kind():
    answers = iter(["a"])
    p = Prompter(interactive=True, prompt_fn=lambda msg: next(answers))
    # First prompt: user says "a" -> apply
    assert p.should_apply(kind="stale-metadata", message="fix a?") is True
    # Subsequent same-kind prompts: no prompt_fn call needed
    assert p.should_apply(kind="stale-metadata", message="fix b?") is True
    assert p.should_apply(kind="stale-metadata", message="fix c?") is True


def test_prompter_a_does_not_leak_to_other_kind():
    answers = iter(["a", "n"])
    p = Prompter(interactive=True, prompt_fn=lambda msg: next(answers))
    assert p.should_apply(kind="stale-metadata", message="fix a?") is True
    # Different kind -> prompt again, answer "n"
    assert p.should_apply(kind="violates", message="fix urllib3?") is False


def test_prompter_q_stops_and_skips_rest():
    p = Prompter(interactive=True, prompt_fn=lambda msg: "q")
    assert p.should_apply(kind="stale-metadata", message="fix?") is False
    assert p.should_quit is True
    # Subsequent calls: no prompt, always False
    assert p.should_apply(kind="stale-metadata", message="next?") is False
    assert p.should_apply(kind="violates", message="next?") is False


def test_prompter_invalid_reprompts():
    answers = iter(["maybe", "x", "y"])
    p = Prompter(interactive=True, prompt_fn=lambda msg: next(answers))
    assert p.should_apply(kind="stale-metadata", message="fix?") is True


def _env_report_with(*problems) -> EnvReport:
    return EnvReport(python_path=None, package_count=len(problems),
                     problems=list(problems))


def test_run_fix_auto_mode_all_succeed(monkeypatch):
    removed_calls = []
    installer_calls = []
    save_state_calls = []

    monkeypatch.setattr(
        "pipu_cli._fix_cli.get_orphan_metadata",
        lambda pp: {"foo": [{"path": "/x.egg-info", "version": "0.1"}]},
    )
    monkeypatch.setattr(
        "pipu_cli._fix_cli.shutil.rmtree",
        lambda p: removed_calls.append(p),
    )
    monkeypatch.setattr(
        "pipu_cli._fix_cli.run_pip_install",
        lambda **kw: (
            installer_calls.append(kw),
            [InstalledResult(
                name="urllib3", version=Version("1.26.20"),
                installed=True, previous_version=Version("2.2.2"),
                failure_reason=None,
            )],
        )[1],
    )
    monkeypatch.setattr(
        "pipu_cli._fix_cli.save_state",
        lambda pkgs, desc: save_state_calls.append((pkgs, desc)),
    )
    monkeypatch.setattr(
        "pipu_cli._fix_cli.inspect_installed_packages",
        lambda **kw: [],
    )

    report = _env_report_with(
        DepProblem(kind="stale-metadata", package="foo",
                   detail="foo has orphaned metadata: /x.egg-info"),
        DepProblem(kind="violates", package="urllib3",
                   detail="urllib3 2.2.2 violates httpx<2",
                   required_by="httpx", specifier="<2",
                   installed_version=Version("2.2.2")),
    )

    console, _buf = _console_with_buf()
    fixes, exit_code = run_fix(
        report=report, console=console, output="human", interactive=False,
    )

    assert exit_code == 0
    assert [f.status for f in fixes] == ["succeeded", "succeeded"]
    assert removed_calls == ["/x.egg-info"]
    assert len(installer_calls) == 1
    # Rollback saved once because plan contained violates.
    assert len(save_state_calls) == 1


def test_run_fix_skips_rollback_when_only_stale(monkeypatch):
    save_state_calls = []
    monkeypatch.setattr(
        "pipu_cli._fix_cli.get_orphan_metadata",
        lambda pp: {"foo": [{"path": "/x.egg-info", "version": "0.1"}]},
    )
    monkeypatch.setattr("pipu_cli._fix_cli.shutil.rmtree", lambda p: None)
    monkeypatch.setattr(
        "pipu_cli._fix_cli.save_state",
        lambda pkgs, desc: save_state_calls.append(1),
    )

    report = _env_report_with(
        DepProblem(kind="stale-metadata", package="foo",
                   detail="foo has orphaned metadata: /x.egg-info"),
    )
    console, _buf = _console_with_buf()
    fixes, exit_code = run_fix(
        report=report, console=console, output="human", interactive=False,
    )
    assert exit_code == 0
    assert len(save_state_calls) == 0


def test_run_fix_failed_install_sets_exit_1(monkeypatch):
    monkeypatch.setattr("pipu_cli._fix_cli.save_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        "pipu_cli._fix_cli.inspect_installed_packages",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "pipu_cli._fix_cli.run_pip_install",
        lambda **kw: [InstalledResult(
            name="urllib3", version=Version("2.2.2"),
            installed=False, previous_version=Version("2.2.2"),
            failure_reason="ResolutionImpossible",
        )],
    )

    report = _env_report_with(
        DepProblem(kind="violates", package="urllib3",
                   detail="urllib3 2.2.2 violates httpx<2",
                   required_by="httpx", specifier="<2",
                   installed_version=Version("2.2.2")),
    )
    console, _buf = _console_with_buf()
    fixes, exit_code = run_fix(
        report=report, console=console, output="human", interactive=False,
    )
    assert exit_code == 1
    assert fixes[0].status == "failed"


def test_run_fix_unfixable_does_not_change_exit(monkeypatch):
    report = _env_report_with(
        DepProblem(kind="missing", package="pandas", detail="pandas missing"),
    )
    console, _buf = _console_with_buf()
    fixes, exit_code = run_fix(
        report=report, console=console, output="human", interactive=False,
    )
    assert exit_code == 0
    assert fixes[0].status == "unfixable"


def test_run_fix_interactive_q_skips_remaining(monkeypatch):
    monkeypatch.setattr("pipu_cli._fix_cli.save_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        "pipu_cli._fix_cli.get_orphan_metadata",
        lambda pp: {
            "a": [{"path": "/a.egg-info", "version": "0.1"}],
            "b": [{"path": "/b.egg-info", "version": "0.1"}],
        },
    )
    monkeypatch.setattr("pipu_cli._fix_cli.shutil.rmtree", lambda p: None)
    monkeypatch.setattr(
        "pipu_cli._fix_cli._prompt_user",
        lambda msg: "q",
    )

    report = _env_report_with(
        DepProblem(kind="stale-metadata", package="a",
                   detail="a has orphaned metadata: /a.egg-info"),
        DepProblem(kind="stale-metadata", package="b",
                   detail="b has orphaned metadata: /b.egg-info"),
    )
    console, _buf = _console_with_buf()
    fixes, exit_code = run_fix(
        report=report, console=console, output="human", interactive=True,
    )
    assert exit_code == 0
    assert all(f.status == "skipped" for f in fixes)
