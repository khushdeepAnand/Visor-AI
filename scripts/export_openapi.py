"""Export the FastAPI OpenAPI schema consumed by the generated TS client.

Usage:
    python scripts/export_openapi.py [--check]

Writes ``frontend/lib/generated/openapi.json``. With ``--check`` it exits
non-zero when the committed schema differs from the live application schema,
which is how API drift fails the build in CI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TARGET = ROOT / "frontend" / "lib" / "generated" / "openapi.json"


def build_schema() -> dict[str, Any]:
    from api.main import app
    spec = app.openapi()
    # Stable output: sort keys so regeneration is diff-friendly.
    return dict(json.loads(json.dumps(spec, sort_keys=True)))


def main(argv: list[str]) -> int:
    check = "--check" in argv
    schema = build_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if check:
        if not TARGET.exists():
            print(f"MISSING: {TARGET.relative_to(ROOT)} (run scripts/export_openapi.py)", file=sys.stderr)
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != rendered:
            print(
                "OpenAPI drift: frontend/lib/generated/openapi.json is stale.\n"
                "Run: python scripts/export_openapi.py && npm run generate:api --prefix frontend",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI schema in sync ({len(schema.get('paths', {}))} paths).")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(schema.get('paths', {}))} paths, "
          f"{len(schema.get('components', {}).get('schemas', {}))} schemas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
