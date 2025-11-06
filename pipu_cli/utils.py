"""
Utility functions for pipu with improved error handling and cross-platform support.

This module provides robust implementations of common operations with proper
error handling, resource cleanup, and platform compatibility.
"""

import subprocess
import sys
import logging
from typing import List, Optional, Tuple
from .config import SUBPROCESS_TIMEOUT, FORCE_KILL_TIMEOUT

logger = logging.getLogger(__name__)


def run_subprocess_safely(
    cmd: List[str],
    timeout: Optional[int] = None,
    check: bool = True,
    capture_output: bool = True
) -> Tuple[int, str, str]:
    """
    Run a subprocess command with proper error handling and cleanup.

    :param cmd: Command and arguments as a list
    :param timeout: Timeout in seconds (None for no timeout)
    :param check: Whether to raise on non-zero exit
    :param capture_output: Whether to capture stdout/stderr
    :returns: Tuple of (return_code, stdout, stderr)
    :raises subprocess.TimeoutExpired: If command times out
    :raises subprocess.CalledProcessError: If check=True and command fails
    """
    if timeout is None:
        timeout = SUBPROCESS_TIMEOUT

    process = None
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            check=check
        )
        return result.returncode, result.stdout or "", result.stderr or ""

    except subprocess.TimeoutExpired as e:
        logger.warning(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        raise

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {' '.join(cmd)}")
        if check:
            raise
        return e.returncode, e.stdout or "", e.stderr or ""

    except (OSError, ValueError) as e:
        logger.error(f"Failed to execute command: {e}")
        raise RuntimeError(f"Failed to execute subprocess: {e}") from e


class ManagedProcess:
    """
    Context manager for subprocess.Popen with guaranteed cleanup.

    Ensures that processes are properly terminated even if exceptions occur.
    """

    def __init__(
        self,
        cmd: List[str],
        timeout: Optional[float] = None,
        **kwargs
    ):
        """
        Initialize a managed process.

        :param cmd: Command and arguments
        :param timeout: Timeout for wait operations
        :param kwargs: Additional arguments for subprocess.Popen
        """
        self.cmd = cmd
        self.timeout = timeout or SUBPROCESS_TIMEOUT
        self.kwargs = kwargs
        self.process: Optional[subprocess.Popen] = None

    def __enter__(self) -> subprocess.Popen:
        """Start the process."""
        try:
            self.process = subprocess.Popen(self.cmd, **self.kwargs)
            return self.process
        except (OSError, ValueError) as e:
            logger.error(f"Failed to start process: {e}")
            raise RuntimeError(f"Failed to start subprocess: {e}") from e

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure process is terminated."""
        if self.process is None:
            return False

        try:
            # Check if process is still running
            if self.process.poll() is None:
                logger.debug(f"Terminating process {self.process.pid}")
                self.process.terminate()

                try:
                    self.process.wait(timeout=FORCE_KILL_TIMEOUT)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Force killing process {self.process.pid}")
                    self.process.kill()
                    try:
                        self.process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        logger.error(f"Failed to kill process {self.process.pid}")

        except Exception as e:
            logger.error(f"Error during process cleanup: {e}")

        return False  # Don't suppress exceptions


def safe_terminal_reset() -> None:
    """
    Safely reset terminal to normal mode.

    Handles platform differences and ensures no crashes on failure.
    """
    try:
        if sys.platform == 'win32':
            # Windows: Enable ANSI escape sequences
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                # Enable virtual terminal processing
                handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
                mode = ctypes.c_ulong()
                kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                mode.value |= 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode)
            except Exception as e:
                logger.debug(f"Failed to enable Windows ANSI support: {e}")

        # Send ANSI reset sequences (works on Unix and Windows 10+)
        try:
            sys.stdout.write('\033[?1049l')  # Exit alternate screen
            sys.stdout.write('\033[?25h')    # Show cursor
            sys.stdout.write('\033[0m')      # Reset all attributes
            sys.stdout.flush()
        except (OSError, ValueError) as e:
            logger.debug(f"Failed to send ANSI sequences: {e}")

        # Unix-like systems: use stty
        if sys.platform != 'win32':
            try:
                subprocess.run(
                    ['stty', 'sane'],
                    capture_output=True,
                    timeout=5.0,
                    check=False
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                # stty not available or failed - not critical
                pass

    except Exception as e:
        # Terminal reset is best-effort - log but don't crash
        logger.debug(f"Terminal reset error: {e}")
