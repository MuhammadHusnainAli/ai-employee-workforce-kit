from pathlib import Path

import pytest

from new_service import TEMPLATES, GenerateError, add_member, render


@pytest.mark.parametrize("lang", ["python", "rust"])
def test_renders_template_with_no_template_names_left(lang: str, tmp_path: Path) -> None:
    dest = tmp_path / "billing-ledger"
    written = render(TEMPLATES[lang], dest, "billing-ledger")
    assert written
    for f in dest.rglob("*"):
        assert "service-template" not in str(f.relative_to(dest))
        assert "service_template" not in str(f.relative_to(dest))
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert "service-template" not in text, f
            assert "service_template" not in text, f
    assert (dest / "migrations/0001_platform_outbox_inbox.up.sql").exists()


def test_python_package_is_renamed(tmp_path: Path) -> None:
    dest = tmp_path / "billing-ledger"
    render(TEMPLATES["python"], dest, "billing-ledger")
    assert (dest / "src/billing_ledger/main.py").exists()
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


@pytest.mark.parametrize("bad", ["Task", "task_service", "-task", "task-", "9task"])
def test_rejects_bad_names(bad: str, tmp_path: Path) -> None:
    with pytest.raises(GenerateError, match="kebab-case"):
        render(TEMPLATES["rust"], tmp_path / "x", bad)


def test_add_member_is_idempotent_and_keeps_the_rest() -> None:
    toml = 'a = 1\n[workspace]\nmembers = [\n    "libs/rust/x",\n]\nb = 2\n'
    once = add_member(toml, "microservices/task-service")
    assert once == add_member(once, "microservices/task-service")
    assert '"libs/rust/x",\n    "microservices/task-service",\n]' in once
    assert once.startswith("a = 1\n") and once.endswith("b = 2\n")
