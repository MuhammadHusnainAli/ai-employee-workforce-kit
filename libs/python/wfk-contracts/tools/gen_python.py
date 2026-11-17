"""Generate Python protobuf and gRPC code from /contracts/proto into src/wfk.

Generated code is committed so `uv sync` alone gives working imports. CI regenerates
and fails if the result differs from what is committed.

Run with: just gen
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from grpc_tools import protoc  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parent
PROTO_ROOT = PACKAGE_ROOT.parent.parent.parent / "contracts" / "proto"
OUT = PACKAGE_ROOT / "src"


def main() -> int:
    protos = sorted(PROTO_ROOT.rglob("*.proto"))
    if not protos:
        print(f"no .proto files under {PROTO_ROOT}", file=sys.stderr)
        return 1

    # Start clean so deleted protos do not leave stale modules behind.
    shutil.rmtree(OUT / "wfk", ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    well_known = Path(protoc.__file__).parent / "_proto"
    args = [
        "grpc_tools.protoc",
        f"-I{PROTO_ROOT}",
        f"-I{well_known}",
        f"--python_out={OUT}",
        f"--pyi_out={OUT}",
        f"--grpc_python_out={OUT}",
        *(p.relative_to(PROTO_ROOT).as_posix() for p in protos),
    ]
    code = protoc.main(args)
    if code != 0:
        return code

    # Make every generated directory an importable package.
    for directory in [OUT / "wfk", *(p for p in (OUT / "wfk").rglob("*") if p.is_dir())]:
        (directory / "__init__.py").touch()
    print(f"generated {len(protos)} proto file(s) into {OUT / 'wfk'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
