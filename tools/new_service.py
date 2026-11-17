"""Create a service from a template: `just new-service <python|rust> <name>`.

Copies templates/<lang>-service into microservices/<name>, renames service-template and
service_template to the new name, and registers the service in the uv or cargo workspace.

The service folders already hold their spec (README.md) and placeholder .gitkeep files.
The spec is never overwritten, and a .gitkeep is removed once its folder gets real files.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = {"python": ROOT / "templates/python-service", "rust": ROOT / "templates/rust-service"}
SKIP = {"target", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
KEEP_EXISTING = {"README.md"}
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class GenerateError(Exception):
    pass


def substitutions(name: str) -> list[tuple[str, str]]:
    snake = name.replace("-", "_")
    return [("service-template", name), ("service_template", snake)]


def render(template: Path, dest: Path, name: str) -> list[Path]:
    """Copy `template` into `dest` with names substituted. Returns the files written."""
    if not NAME.match(name):
        raise GenerateError(f"service name must be kebab-case (task-service): {name!r}")
    subs = substitutions(name)

    def rename(text: str) -> str:
        for old, new in subs:
            text = text.replace(old, new)
        return text

    written: list[Path] = []
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        if any(part in SKIP for part in rel.parts) or src.is_dir():
            continue
        target = dest / rename(str(rel))
        if target.exists():
            if target.name in KEEP_EXISTING:
                continue
            raise GenerateError(f"{target} already exists; refusing to overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(rename(src.read_text(encoding="utf-8")), encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src, target)
        written.append(target)

    # A placeholder is no longer needed once its folder (or a subfolder) has real files.
    for keep in dest.rglob(".gitkeep"):
        folder = keep.parent
        if any(p != keep and p.is_file() and folder in p.parents for p in written):
            keep.unlink()
    return written


def add_member(toml: str, member: str) -> str:
    """Add `member` to the first `members = [...]` list in a TOML document. Idempotent."""
    match = re.search(r"members\s*=\s*\[(?P<body>[^\]]*)\]", toml)
    if not match:
        raise GenerateError("no members = [...] list found")
    items = re.findall(r'"([^"]+)"', match["body"])
    if member in items:
        return toml
    items.append(member)
    body = "".join(f'    "{i}",\n' for i in items)
    return toml[: match.start()] + f"members = [\n{body}]" + toml[match.end() :]


def register(lang: str, name: str, root: Path = ROOT) -> Path:
    manifest = root / ("pyproject.toml" if lang == "python" else "Cargo.toml")
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(add_member(text, f"microservices/{name}"), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a service from a template.")
    parser.add_argument("lang", choices=sorted(TEMPLATES))
    parser.add_argument("name", help="kebab-case service name, e.g. task-service")
    args = parser.parse_args(argv)
    dest = ROOT / "microservices" / args.name
    try:
        files = render(TEMPLATES[args.lang], dest, args.name)
        manifest = register(args.lang, args.name)
    except GenerateError as exc:
        print(f"new-service: {exc}", file=sys.stderr)
        return 1
    print(f"created {len(files)} files in {dest.relative_to(ROOT)}")
    print(f"registered in {manifest.name}")
    print("next: uv sync --all-packages" if args.lang == "python" else "next: cargo build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
