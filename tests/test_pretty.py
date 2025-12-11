"""Tests for pretty.py module."""

import pytest
from io import StringIO
from unittest.mock import Mock

from pipu_cli.pretty import ConsoleStream


def test_console_stream_write_text():
    """Test ConsoleStream writes text to console."""
    mock_console = Mock()
    stream = ConsoleStream(mock_console)

    stream.write("Hello world\n")

    mock_console.print.assert_called_once_with("Hello world\n", end="")


def test_console_stream_write_empty_string():
    """Test ConsoleStream ignores empty strings."""
    mock_console = Mock()
    stream = ConsoleStream(mock_console)

    stream.write("")
    stream.write("   ")

    mock_console.print.assert_not_called()


def test_console_stream_flush():
    """Test ConsoleStream flush does nothing."""
    mock_console = Mock()
    stream = ConsoleStream(mock_console)

    # Should not raise
    stream.flush()
