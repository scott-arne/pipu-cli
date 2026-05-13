"""Shared pip subprocess runner.

Internal module (leading underscore) consumed by package_management. Owns
Popen lifecycle, stream draining, timeout handling, and Ctrl-C semantics
for every pip invocation pipu makes.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, IO, Literal, Optional, Protocol


class SupportsWriteFlush(Protocol):
    """Narrow writer protocol for streaming pip output.

    Matches any object that can receive text chunks and flush them,
    including ``sys.stdout``, ``io.StringIO``, and any caller-defined
    ``write(text) -> int | None`` / ``flush() -> None`` implementation.
    """

    def write(self, text: str, /) -> int | None: ...
    def flush(self) -> None: ...


@dataclass(frozen=True)
class PipResult:
    """Outcome of a pip subprocess invocation.

    :param returncode: Process exit code (may be negative on signal).
    :param stdout: Captured stdout. Non-empty only when ``stream_output=False``.
    :param stderr: Captured stderr. Non-empty only when ``stream_output=False``.
    :param timed_out: True if the process was killed by the timeout path.
    :param interrupted: True if the process was killed via ``InterruptToken``.
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    interrupted: bool = False


class InterruptToken:
    """Cross-thread interrupt signal for group runs.

    The main thread's Ctrl-C handler calls ``set()``; every worker thread's
    ``run_pip`` call polls via its registered live process and terminates
    its subprocess when the token flips. Thread-safe.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._live: list[subprocess.Popen[str]] = []

    def set(self) -> None:
        self._event.set()
        self.terminate_all()

    def is_set(self) -> bool:
        return self._event.is_set()

    def register(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._live.append(proc)

    def deregister(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            try:
                self._live.remove(proc)
            except ValueError:
                pass

    def terminate_all(self) -> None:
        with self._lock:
            procs = list(self._live)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


def _drain(
    stream: IO[str],
    sink: list[str] | None,
    tee: Optional[SupportsWriteFlush],
    on_activity: Optional[Callable[[], None]] = None,
    line_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Read ``stream`` line-by-line, append to ``sink``, optionally mirror to ``tee``.

    :param stream: File-like handle attached to the subprocess pipe.
    :param sink: Capture buffer; pass ``None`` to discard (used when streaming,
        so ``PipResult.stdout``/``stderr`` stay empty per the contract).
    :param tee: Optional stream to mirror lines to in real time.
    :param on_activity: Optional callback invoked whenever a line arrives.
    :param line_callback: Optional observer invoked with each output line.
    """
    try:
        for line in iter(stream.readline, ""):
            if on_activity is not None:
                on_activity()
            if line_callback is not None:
                try:
                    line_callback(line)
                except Exception:
                    # Progress observers must not poison subprocess draining.
                    pass
            if sink is not None:
                sink.append(line)
            if tee is not None:
                tee.write(line)
                tee.flush()
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _cleanup(proc: subprocess.Popen[str]) -> None:
    """Best-effort terminate-then-kill, used when the caller abandons the process.

    :param proc: Subprocess handle to clean up. Idempotent; safe to call on an
        already-exited process.
    """
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return  # Already gone.
    except Exception:
        pass  # Non-fatal: fall through to kill fallback.
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass


def run_pip(
    argv: list[str],
    *,
    python_path: str | None = None,
    output_stream: Optional[SupportsWriteFlush] = None,
    timeout: float = 300,
    stream_output: bool = True,
    interrupt_token: InterruptToken | None = None,
    timeout_mode: Literal["wall", "idle"] = "wall",
    line_callback: Optional[Callable[[str], None]] = None,
) -> PipResult:
    """Run pip (or any Python command) as a subprocess.

    :param argv: Arguments following the Python interpreter. For a pip call,
        pass e.g. ``["-m", "pip", "install", "requests"]``.
    :param python_path: Interpreter to invoke. Defaults to ``sys.executable``.
    :param output_stream: If ``stream_output`` is True, lines are tee'd here.
    :param timeout: Timeout in seconds. With ``timeout_mode="wall"``, this is
        a hard wall-clock limit. With ``timeout_mode="idle"``, this is the
        maximum time allowed without stdout/stderr activity.
    :param stream_output: When True, drain stdout/stderr in real time (captured
        strings stay empty). When False, capture both fully.
    :param interrupt_token: Optional token; if ``set()`` during the call, the
        subprocess is terminated and ``PipResult.interrupted`` is True.
    :param timeout_mode: Whether to apply ``timeout`` as a wall-clock or idle
        limit.
    :param line_callback: Optional observer invoked with each stdout/stderr line.
    :returns: A :class:`PipResult`. If both an interrupt and a timeout could
        apply, ``interrupted`` wins -- user cancel is treated as more
        semantically meaningful than wall-clock expiry, so ``timed_out`` is
        reported as False in that case.
    """

    if timeout_mode not in {"wall", "idle"}:
        raise ValueError(f"Unsupported timeout_mode: {timeout_mode}")

    py = python_path or sys.executable
    cmd = [py, *argv]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    if interrupt_token is not None:
        interrupt_token.register(proc)
        if interrupt_token.is_set():
            interrupt_token.deregister(proc)
            _cleanup(proc)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            return PipResult(returncode=-1, stdout="", stderr="", interrupted=True)

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    tee = output_stream if stream_output else None
    # Per the PipResult contract, captured strings stay empty in stream mode.
    out_sink: list[str] | None = None if stream_output else stdout_buf
    err_sink: list[str] | None = None if stream_output else stderr_buf

    activity_lock = threading.Lock()
    last_activity = time.monotonic()

    def mark_activity() -> None:
        nonlocal last_activity
        with activity_lock:
            last_activity = time.monotonic()

    def idle_seconds() -> float:
        with activity_lock:
            return time.monotonic() - last_activity

    t_out = threading.Thread(
        target=_drain,
        args=(proc.stdout, out_sink, tee, mark_activity, line_callback),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_drain,
        args=(proc.stderr, err_sink, tee, mark_activity, line_callback),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    timed_out = False
    interrupted = False
    try:
        if timeout_mode == "wall":
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _cleanup(proc)
        else:
            while proc.poll() is None:
                remaining = timeout - idle_seconds()
                if remaining <= 0:
                    timed_out = True
                    _cleanup(proc)
                    break
                try:
                    proc.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
    except KeyboardInterrupt:
        interrupted = True
        _cleanup(proc)
        raise
    finally:
        if interrupt_token is not None:
            if interrupt_token.is_set():
                interrupted = True
                timed_out = False  # user-cancel wins over wall-clock timeout
                _cleanup(proc)
            interrupt_token.deregister(proc)
        t_out.join(timeout=2)
        t_err.join(timeout=2)

    return PipResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout="".join(stdout_buf),
        stderr="".join(stderr_buf),
        timed_out=timed_out,
        interrupted=interrupted,
    )
