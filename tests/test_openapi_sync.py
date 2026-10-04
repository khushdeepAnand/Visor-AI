"""The committed OpenAPI schema must match the live application schema.

This is the backend half of the API drift gate: the frontend generated client
(`frontend/lib/generated/routes.ts`) is built from the committed
`openapi.json`, so a backend route/contract change that is not exported fails
here, and a stale generated client fails `npm run check:api` (prebuild).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "frontend" / "lib" / "generated" / "openapi.json"


def test_committed_openapi_schema_matches_app():
    assert SCHEMA_PATH.exists(), (
        "frontend/lib/generated/openapi.json is missing; run: python scripts/export_openapi.py"
    )
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    from scripts.export_openapi import build_schema

    live = build_schema()

    committed_paths = set(committed.get("paths", {}))
    live_paths = set(live.get("paths", {}))
    added = sorted(live_paths - committed_paths)
    removed = sorted(committed_paths - live_paths)
    assert not added and not removed, (
        f"OpenAPI drift. Added on backend: {added or 'none'}; "
        f"removed from schema: {removed or 'none'}. "
        "Run: python scripts/export_openapi.py && npm run generate:api --prefix frontend"
    )

    committed_schemas = set((committed.get("components") or {}).get("schemas", {}))
    live_schemas = set((live.get("components") or {}).get("schemas", {}))
    assert committed_schemas == live_schemas, (
        f"Schema drift. New: {sorted(live_schemas - committed_schemas)}; "
        f"gone: {sorted(committed_schemas - live_schemas)}."
    )

    # Deep equality of the operation contracts (request/response shapes).
    for path in sorted(live_paths):
        assert committed["paths"][path] == live["paths"][path], (
            f"OpenAPI operation changed for {path}; re-run scripts/export_openapi.py"
        )
    assert committed.get("components") == live.get("components")


def test_generated_route_client_exists():
    routes = ROOT / "frontend" / "lib" / "generated" / "routes.ts"
    assert routes.exists(), "frontend/lib/generated/routes.ts missing; run npm run generate:api"
    text = routes.read_text(encoding="utf-8")
    assert "AUTO-GENERATED" in text
    assert "RouteKey" in text
