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


def test_unreadable_field_falls_back_to_classifiers() -> None:
    from email.message import Message
    from typing import Any, cast

    from check_licenses import licence_of

    class Dist:
        def __init__(self, field: str, classifiers: list[str]) -> None:
            self.metadata = Message()
            self.metadata["License"] = field
            for c in classifiers:
                self.metadata["Classifier"] = c

    dual = Dist("Dual License", ["License :: OSI Approved :: Apache Software License"])
    assert licence_of(cast(Any, dual)) == "Apache Software License"
    gpl = Dist("GPL-3.0-only", [])
    assert licence_of(cast(Any, gpl)) == "GPL-3.0-only"
