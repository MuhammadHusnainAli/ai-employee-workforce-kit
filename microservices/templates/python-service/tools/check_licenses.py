"""Fail if any installed Python package is not under an allowed licence (ADR-0007).

    uv run python tools/check_licenses.py

Reads each package's metadata: the SPDX `License-Expression` first, then the `License`
field, then trove classifiers. Rust services check theirs with cargo-deny (deny.toml)
against the same list.
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import Distribution, distributions
from pathlib import Path

# OSI-approved licences allowed for dependencies. Keep in step with deny.toml.
ALLOWED = {
    "Apache-2.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "Zlib",
    "MPL-2.0",
    "PSF-2.0",
    "PostgreSQL",
    "Unicode-3.0",
    # Weak copyleft, allowed for libraries used unmodified (psycopg). Not for code we fork.
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
}

# Free-text licence names and classifiers seen in the wild, mapped to SPDX ids.
ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
    "isc license (iscl)": "ISC",
}


def normalise(text: str) -> str:
    text = text.strip()
    return ALIASES.get(text.lower(), text)


def expression_allowed(expr: str) -> bool:
    """SPDX expression check: OR needs any side allowed, AND needs every side allowed."""
    expr = expr.strip().strip("()")
    if " OR " in expr:
        return any(expression_allowed(p) for p in expr.split(" OR "))
    if " AND " in expr:
        return all(expression_allowed(p) for p in expr.split(" AND "))
    return normalise(expr) in ALLOWED


def licence_of(dist: Distribution) -> str:
    """The best licence statement in the metadata.

    SPDX `License-Expression` wins. Otherwise the free-text `License` field is used when it is
    a licence we can read, and the trove classifiers when it is not (a field like "Dual
    License" says nothing; the classifiers name both licences).
    """
    meta = dist.metadata
    if expr := meta.get("License-Expression"):
        return expr
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    field = (meta.get("License") or "").strip()
    short = bool(field) and len(field.splitlines()) == 1 and len(field) < 60
    if short and (expression_allowed(field) or not classifiers):
        return field
    return " OR ".join(classifiers) or field


PROJECT = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["name"]


def ours(dist: Distribution) -> bool:
    """This service itself is Apache-2.0 by definition."""
    return dist.metadata["Name"] == PROJECT


def main() -> int:
    bad: list[str] = []
    count = 0
    for dist in distributions():
        if ours(dist):
            continue
        count += 1
        lic = licence_of(dist)
        if not lic or not expression_allowed(lic):
            bad.append(f"  {dist.metadata['Name']} {dist.version}: {lic or 'unknown licence'}")
    if bad:
        print("Packages with licences outside ADR-0007:", *sorted(bad), sep="\n", file=sys.stderr)
        return 1
    print(f"licences ok: {count} third-party packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
