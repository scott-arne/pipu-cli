"""Tests for pretty.py module."""

import pytest
from io import StringIO
from unittest.mock import Mock

from pipu_cli.pretty import ConsoleStream, _parse_selection


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


class TestParseSelection:
    """Tests for _parse_selection function."""

    def test_single_number(self):
        """Test parsing a single number."""
        assert _parse_selection("1", 5) == [0]
        assert _parse_selection("3", 5) == [2]

    def test_comma_separated_numbers(self):
        """Test parsing comma-separated numbers."""
        assert _parse_selection("1,2,3", 5) == [0, 1, 2]
        assert _parse_selection("1, 3, 5", 5) == [0, 2, 4]

    def test_range(self):
        """Test parsing a range."""
        assert _parse_selection("1-3", 5) == [0, 1, 2]
        assert _parse_selection("2-4", 5) == [1, 2, 3]

    def test_range_with_spaces(self):
        """Test parsing a range with spaces."""
        assert _parse_selection("1 - 3", 5) == [0, 1, 2]

    def test_reversed_range(self):
        """Test parsing a reversed range (e.g., 3-1)."""
        assert _parse_selection("3-1", 5) == [0, 1, 2]

    def test_mixed_ranges_and_numbers(self):
        """Test parsing mixed ranges and numbers."""
        assert _parse_selection("1-3, 5", 5) == [0, 1, 2, 4]
        assert _parse_selection("1, 3-5", 5) == [0, 2, 3, 4]
        assert _parse_selection("1, 3-4, 5", 5) == [0, 2, 3, 4]

    def test_duplicates_removed(self):
        """Test that duplicate selections are removed."""
        assert _parse_selection("1, 1, 2", 5) == [0, 1]
        assert _parse_selection("1-3, 2", 5) == [0, 1, 2]

    def test_out_of_range_ignored(self):
        """Test that out-of-range indices are ignored."""
        assert _parse_selection("1, 10", 5) == [0]
        assert _parse_selection("0, 1", 5) == [0]  # 0 is out of range (1-based)
        assert _parse_selection("1-10", 5) == [0, 1, 2, 3, 4]

    def test_empty_parts_ignored(self):
        """Test that empty parts are ignored."""
        assert _parse_selection("1,,3", 5) == [0, 2]
        assert _parse_selection("1,  ,3", 5) == [0, 2]

    def test_invalid_range_raises(self):
        """Test that invalid range format raises ValueError."""
        with pytest.raises(ValueError):
            _parse_selection("1-2-3", 5)

    def test_invalid_number_raises(self):
        """Test that invalid number raises ValueError."""
        with pytest.raises(ValueError):
            _parse_selection("abc", 5)

    def test_whitespace_handling(self):
        """Test handling of various whitespace."""
        assert _parse_selection("  1  ,  2  ", 5) == [0, 1]
        assert _parse_selection("1-3,4", 5) == [0, 1, 2, 3]
