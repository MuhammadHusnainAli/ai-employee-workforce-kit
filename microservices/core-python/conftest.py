"""pytest setup for this service."""

import asyncio
import faulthandler
import sys
from collections.abc import Callable, Mapping

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    # psycopg's async driver cannot use the Proactor loop that Windows defaults to.
    # Production runs on Linux, where the default loop is already a selector loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    # On Windows, faulthandler reports every access-violation exception, including ones the
    # Temporal SDK's Rust core raises and handles itself, flooding the output although
    # nothing crashed. It stays on everywhere else (CI runs on Linux).
    if sys.platform == "win32":
        faulthandler.disable()
