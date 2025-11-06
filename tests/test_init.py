import logging
from pipu_cli import LevelSpecificFormatter, __version__


def test_version():
    """
    Test that version is available and not None.
    
    :returns: None
    """
    assert __version__ is not None


def test_level_specific_formatter_info_level():
    """
    Test LevelSpecificFormatter with INFO level log record.
    
    :returns: None
    """
    formatter = LevelSpecificFormatter()
    record = logging.LogRecord(
        name="test", 
        level=logging.INFO, 
        pathname="", 
        lineno=0, 
        msg="test message", 
        args=(), 
        exc_info=None
    )
    
    formatted = formatter.format(record)
    assert formatted == "test message"


def test_level_specific_formatter_debug_level():
    """
    Test LevelSpecificFormatter with DEBUG level log record.
    
    :returns: None
    """
    formatter = LevelSpecificFormatter()
    record = logging.LogRecord(
        name="test", 
        level=logging.DEBUG, 
        pathname="", 
        lineno=0, 
        msg="test message", 
        args=(), 
        exc_info=None
    )
    
    formatted = formatter.format(record)
    assert formatted == "DEBUG: test message"


def test_level_specific_formatter_warning_level():
    """
    Test LevelSpecificFormatter with WARNING level log record.
    
    :returns: None
    """
    formatter = LevelSpecificFormatter()
    record = logging.LogRecord(
        name="test", 
        level=logging.WARNING, 
        pathname="", 
        lineno=0, 
        msg="test message", 
        args=(), 
        exc_info=None
    )
    
    formatted = formatter.format(record)
    assert formatted == "WARNING: test message"


def test_level_specific_formatter_error_level():
    """
    Test LevelSpecificFormatter with ERROR level log record.
    
    :returns: None
    """
    formatter = LevelSpecificFormatter()
    record = logging.LogRecord(
        name="test", 
        level=logging.ERROR, 
        pathname="", 
        lineno=0, 
        msg="test message", 
        args=(), 
        exc_info=None
    )
    
    formatted = formatter.format(record)
    assert formatted == "ERROR: test message"


def test_level_specific_formatter_critical_level():
    """
    Test LevelSpecificFormatter with CRITICAL level log record.
    
    :returns: None
    """
    formatter = LevelSpecificFormatter()
    record = logging.LogRecord(
        name="test", 
        level=logging.CRITICAL, 
        pathname="", 
        lineno=0, 
        msg="test message", 
        args=(), 
        exc_info=None
    )
    
    formatted = formatter.format(record)
    assert formatted == "CRITICAL: test message"