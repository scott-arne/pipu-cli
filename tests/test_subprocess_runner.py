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
