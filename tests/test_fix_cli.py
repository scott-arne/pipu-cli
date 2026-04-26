"""Tests for the _fix_cli module."""

from io import StringIO

from packaging.version import Version
from rich.console import Console

from pipu_cli._fix_cli import Prompter, render_fix_line, render_fix_summary
from pipu_cli.fixer import FixResult
from pipu_cli.package_management import DepProblem


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
