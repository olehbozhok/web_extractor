"""Scaffold smoke test: verifies the test runner and package import work."""

import web_extractor


def test_package_is_importable() -> None:
    """The package must import cleanly on the bare scaffold."""
    assert web_extractor.__doc__ is not None
