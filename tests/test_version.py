def test_version():
    """
    Test that version is available and not None.
    
    :returns: None
    """
    from pipu import __version__
    assert __version__ is not None
