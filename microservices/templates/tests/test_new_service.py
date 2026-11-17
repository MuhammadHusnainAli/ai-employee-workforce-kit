"""Run from microservices/templates:  uvx pytest tests"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from new_service import TEMPLATES, GenerateError, render  # noqa: E402


@pytest.mark.parametrize("lang", ["python", "rust"])
def test_renders_template_with_no_template_names_left(lang: str, tmp_path: Path) -> None:
    dest = tmp_path / "billing-ledger"
    written = render(TEMPLATES[lang], dest, "billing-ledger")
    assert written
    for f in dest.rglob("*"):
        rel = f.relative_to(dest).as_posix()
        assert "service-template" not in rel and "service_template" not in rel
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            assert "service-template" not in text, f
            assert "service_template" not in text, f


@pytest.mark.parametrize(
    ("lang", "manifest", "lock"),
    [("python", "pyproject.toml", "uv.lock"), ("rust", "Cargo.toml", "Cargo.lock")],
)
def test_service_is_self_contained(lang: str, manifest: str, lock: str, tmp_path: Path) -> None:
    dest = tmp_path / "billing-ledger"
    render(TEMPLATES[lang], dest, "billing-ledger")
    for required in (manifest, lock, "justfile", "Dockerfile", ".dockerignore"):
        assert (dest / required).is_file(), required
    assert (dest / "contracts/proto/wfk/common/v1/common.proto").is_file()
    assert (dest / "migrations/0001_platform_outbox_inbox.up.sql").is_file()
    text = (dest / manifest).read_text(encoding="utf-8")
    assert "../" not in text, "a service must not point outside its own folder"


def test_python_packages_are_renamed(tmp_path: Path) -> None:
    dest = tmp_path / "billing-ledger"
    render(TEMPLATES["python"], dest, "billing-ledger")
    assert (dest / "src/billing_ledger/main.py").is_file()
    assert (dest / "src/wfk_platform/outbox.py").is_file()
    assert 'name = "billing-ledger"' in (dest / "pyproject.toml").read_text(encoding="utf-8")


def test_keeps_the_spec_and_clears_filled_placeholders(tmp_path: Path) -> None:
    dest = tmp_path / "task-service"
    (dest / "migrations").mkdir(parents=True)
    (dest / "workflows").mkdir()
    (dest / "README.md").write_text("# the spec", encoding="utf-8")
    (dest / "migrations/.gitkeep").touch()
    (dest / "workflows/.gitkeep").touch()

    render(TEMPLATES["rust"], dest, "task-service")

    assert (dest / "README.md").read_text(encoding="utf-8") == "# the spec"
    assert not (dest / "migrations/.gitkeep").exists()  # now has migrations
    assert (dest / "workflows/.gitkeep").exists()  # still empty, placeholder stays


def test_refuses_to_overwrite_code(tmp_path: Path) -> None:
    dest = tmp_path / "task-service"
    render(TEMPLATES["rust"], dest, "task-service")
    with pytest.raises(GenerateError, match="already exists"):
        render(TEMPLATES["rust"], dest, "task-service")


@pytest.mark.parametrize("bad", ["Task", "task_service", "-task", "task-", "9task", "templates"])
def test_rejects_bad_names(bad: str, tmp_path: Path) -> None:
    with pytest.raises(GenerateError):
        render(TEMPLATES["rust"], tmp_path / "x", bad)


def test_writes_lf_line_endings(tmp_path: Path) -> None:
    dest = tmp_path / "x-service"
    render(TEMPLATES["python"], dest, "x-service")
    assert b"\r\n" not in (dest / "pyproject.toml").read_bytes()
