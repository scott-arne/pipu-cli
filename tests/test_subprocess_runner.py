import sys
import threading
import time
from pathlib import Path

from pipu_cli._subprocess import InterruptToken, run_pip

def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake_pip.py"
    p.write_text(body)
    return p

def test_success_returns_captured_output(tmp_path):
    s = _script(tmp_path, "import sys; sys.stdout.write('hello'); sys.stderr.write('warn')")
    r = run_pip([str(s)], python_path=sys.executable, stream_output=False, timeout=10)
    assert r.returncode == 0
    assert "hello" in r.stdout
    assert "warn" in r.stderr
    assert not r.timed_out and not r.interrupted

def test_nonzero_propagates(tmp_path):
    s = _script(tmp_path, "import sys; sys.exit(7)")
    r = run_pip([str(s)], python_path=sys.executable, stream_output=False, timeout=10)
    assert r.returncode == 7

def test_timeout_sets_flag(tmp_path):
    s = _script(tmp_path, "import time; time.sleep(5)")
    r = run_pip([str(s)], python_path=sys.executable, stream_output=False, timeout=1)
    assert r.timed_out is True
    assert r.returncode != 0

def test_idle_timeout_allows_active_output(tmp_path):
    s = _script(
        tmp_path,
        (
            "import time\n"
            "for i in range(4):\n"
            "    print(f'tick {i}', flush=True)\n"
            "    time.sleep(0.2)\n"
        ),
    )
    r = run_pip(
        [str(s)],
        python_path=sys.executable,
        stream_output=False,
        timeout=0.3,
        timeout_mode="idle",
    )
    assert r.returncode == 0
    assert r.timed_out is False
    assert "tick 3" in r.stdout

def test_idle_timeout_can_ignore_non_activity_output(tmp_path):
    s = _script(
        tmp_path,
        (
            "import time\n"
            "for i in range(20):\n"
            "    print(f'Collecting dependency-{i}', flush=True)\n"
            "    time.sleep(0.1)\n"
        ),
    )
    r = run_pip(
        [str(s)],
        python_path=sys.executable,
        stream_output=False,
        timeout=0.3,
        timeout_mode="idle",
        idle_activity_filter=lambda line: line.startswith("Progress "),
    )
    assert r.timed_out is True
    assert r.returncode != 0
    assert "Collecting dependency" in r.stdout

def test_idle_timeout_can_count_partial_status_output(tmp_path):
    s = _script(
        tmp_path,
        (
            "import sys, time\n"
            "sys.stdout.write('Preparing metadata (pyproject.toml) ... |')\n"
            "sys.stdout.flush()\n"
            "time.sleep(0.15)\n"
            "sys.stdout.write('\\b/')\n"
            "sys.stdout.flush()\n"
            "time.sleep(0.15)\n"
            "sys.stdout.write('\\b-')\n"
            "sys.stdout.flush()\n"
            "time.sleep(0.15)\n"
            "print(' done')\n"
        ),
    )
    r = run_pip(
        [str(s)],
        python_path=sys.executable,
        stream_output=False,
        timeout=0.25,
        timeout_mode="idle",
        idle_activity_filter=lambda text: "Preparing metadata" in text,
    )
    assert r.timed_out is False
    assert r.returncode == 0
    assert "Preparing metadata" in r.stdout

def test_idle_timeout_fires_after_output_stalls(tmp_path):
    s = _script(
        tmp_path,
        "import time; print('started', flush=True); time.sleep(5)",
    )
    r = run_pip(
        [str(s)],
        python_path=sys.executable,
        stream_output=False,
        timeout=0.3,
        timeout_mode="idle",
    )
    assert r.timed_out is True
    assert r.returncode != 0
    assert "started" in r.stdout

def test_interrupt_via_token(tmp_path):
    s = _script(tmp_path, "import time\nfor _ in range(20): time.sleep(0.1)")
    token = InterruptToken()
    done = threading.Event()
    result: dict = {}
    def go():
        result["r"] = run_pip([str(s)], python_path=sys.executable, stream_output=False, timeout=10, interrupt_token=token)
        done.set()
    threading.Thread(target=go, daemon=True).start()
    time.sleep(0.3)
    token.set()
    done.wait(timeout=5)
    assert result["r"].interrupted is True

def test_stream_output_writes_to_stream(tmp_path):
    import io
    s = _script(tmp_path, "print('streamed'); import sys; sys.stdout.flush()")
    buf = io.StringIO()
    r = run_pip([str(s)], python_path=sys.executable, stream_output=True, output_stream=buf, timeout=10)
    assert r.returncode == 0
    assert "streamed" in buf.getvalue()
    assert r.stdout == ""
    assert r.stderr == ""

def test_line_callback_receives_captured_output(tmp_path):
    s = _script(tmp_path, "print('Progress 1024 of 2048', flush=True)")
    lines = []

    r = run_pip(
        [str(s)],
        python_path=sys.executable,
        stream_output=False,
        timeout=10,
        line_callback=lines.append,
    )

    assert r.returncode == 0
    assert lines == ["Progress 1024 of 2048\n"]
    assert "Progress 1024 of 2048" in r.stdout

def test_interrupt_already_set_returns_early(tmp_path):
    """If the token is already set when run_pip starts, we short-circuit to interrupted=True."""
    s = _script(tmp_path, "import time\nfor _ in range(50): time.sleep(0.1)")
    token = InterruptToken()
    token.set()  # Already tripped before run_pip.
    r = run_pip(
        [str(s)], python_path=sys.executable, stream_output=False,
        timeout=10, interrupt_token=token,
    )
    assert r.interrupted is True
    assert r.returncode != 0
