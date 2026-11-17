import pytest

from check_licenses import expression_allowed, main


@pytest.mark.parametrize(
    "expr",
    ["MIT", "Apache-2.0", "Apache-2.0 OR BSD-2-Clause", "MIT AND BSD-3-Clause", "BSD License"],
)
def test_allowed(expr: str) -> None:
    assert expression_allowed(expr)


@pytest.mark.parametrize(
    "expr", ["GPL-3.0-only", "AGPL-3.0-only", "BUSL-1.1", "SSPL-1.0", "MIT AND GPL-2.0-only", ""]
)
def test_refused(expr: str) -> None:
    assert not expression_allowed(expr)


def test_current_environment_passes() -> None:
    assert main() == 0
