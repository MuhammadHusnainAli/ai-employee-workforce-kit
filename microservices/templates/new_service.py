"""Create a service from a template: `just new-service <python|rust> <name>` at the repo root.

Copies microservices/templates/<lang>-service into microservices/<name> and renames
service-template / service_template to the new name. The new service is self-contained: its
own pyproject.toml or Cargo.toml, lockfile, copy of the platform code, contracts, migrations,
justfile and Dockerfile. Nothing is registered anywhere else.

The service folders already hold their spec (README.md) and placeholder .gitkeep files. The
spec is never overwritten, and a .gitkeep is removed once its folder gets real files.

Standard library only, so it runs with any Python 3.11+.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MICROSERVICES = HERE.parent
TEMPLATES = {"python": HERE / "python-service", "rust": HERE / "rust-service"}
SKIP = {"target", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
KEEP_EXISTING = {"README.md"}
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
RESERVED = {"templates"}


class GenerateError(Exception):
    pass


def substitutions(name: str) -> list[tuple[str, str]]:
    return [("service-template", name), ("service_template", name.replace("-", "_"))]


def render(template: Path, dest: Path, name: str) -> list[Path]:
    """Copy `template` into `dest` with names substituted. Returns the files written."""
    if not NAME.match(name):
        raise GenerateError(f"service name must be kebab-case (task-service): {name!r}")
    if name in RESERVED:
        raise GenerateError(f"{name!r} is reserved")
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
        target = dest / rename(rel.as_posix())
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
        if any(folder in p.parents for p in written):
            keep.unlink()
    return written


FORMATTERS = {
    # The new name can be longer or sort differently than service-template, which changes
    # line wrapping and import order. One formatter pass makes the copy lint-clean.
    "python": [
        ["uvx", "ruff@0.17.5", "check", "--fix", "--select", "I", "--quiet", "."],
        ["uvx", "ruff@0.17.5", "format", "--quiet", "."],
    ],
    "rust": [["cargo", "fmt"]],
}


def format_service(lang: str, dest: Path) -> None:
    for cmd in FORMATTERS[lang]:
        try:
            subprocess.run(cmd, cwd=dest, check=True)  # noqa: S603 - fixed command list
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"new-service: formatter failed ({' '.join(cmd)}): {exc}", file=sys.stderr)
            print("new-service: run the formatter in the new service by hand", file=sys.stderr)
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a service from a template.")
    parser.add_argument("lang", choices=sorted(TEMPLATES))
    parser.add_argument("name", help="kebab-case service name, e.g. task-service")
    parser.add_argument("--no-format", action="store_true", help="skip the formatter pass")
    args = parser.parse_args(argv)
    dest = MICROSERVICES / args.name
    try:
        files = render(TEMPLATES[args.lang], dest, args.name)
    except GenerateError as exc:
        print(f"new-service: {exc}", file=sys.stderr)
        return 1
    if not args.no_format:
        format_service(args.lang, dest)
    print(f"created {len(files)} files in microservices/{args.name}")
    print(f"next: cd microservices/{args.name} && just ci")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
