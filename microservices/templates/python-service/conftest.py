"""Repo-wide pytest setup, shared by every Python library and service."""

import asyncio
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
