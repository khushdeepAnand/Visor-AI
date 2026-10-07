"""OpenAPI linting and frontend/backend drift gates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from api.main import app


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SOURCES = (ROOT / "frontend" / "app", ROOT / "frontend" / "components", ROOT / "frontend" / "lib")
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
PATH_PATTERN = re.compile(r"/api/v1/[A-Za-z0-9_./{}$()\-]+")
METHOD_PATTERN = re.compile(r"method\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)[\"']", re.I)
# Reviewed additions: bulk accounts, compliance read/update and queue
# read/trigger/replay, alongside the prior model/role/metrics routes.
EXPECTED_PATH_METHOD_SHA256 = "3220690e31298d92cafd8751f44dfbfca3e8b5592f893243e9bbdab0388b864a"


def _operations(schema: dict[str, Any]) -> dict[str, set[str]]:
    return {
        path: {key.upper() for key in item if key.lower() in HTTP_METHODS}
        for path, item in schema["paths"].items()
    }


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\r\n]*", "", source)


def _segments_match(frontend_path: str, backend_path: str) -> bool:
    frontend = frontend_path.strip("/").split("/")
    backend = backend_path.strip("/").split("/")
    if len(frontend) != len(backend):
        return False
    for actual, expected in zip(frontend, backend, strict=True):
        if actual.startswith("${"):
            if "{" not in expected:
                return False
        elif "${" in actual:
            if actual.split("${", 1)[0] != expected:
                return False
        elif actual != expected:
            return False
    return True


def _frontend_calls() -> list[tuple[Path, int, str, str]]:
    calls: list[tuple[Path, int, str, str]] = []
    for directory in FRONTEND_SOURCES:
        for path in sorted((*directory.rglob("*.ts"), *directory.rglob("*.tsx"))):
            # The generated OpenAPI client is derived FROM the schema below,
            # so scanning it would be circular; it has its own drift gate
            # (`npm run check:api` / tests/test_openapi_sync.py).
            if "generated" in path.relative_to(ROOT).parts:
                continue
            source = _without_comments(path.read_text(encoding="utf-8"))
            matches = list(PATH_PATTERN.finditer(source))
            for index, match in enumerate(matches):
                route = match.group(0).split("?", 1)[0]
                next_start = matches[index + 1].start() if index + 1 < len(matches) else len(source)
                tail = source[match.end():min(next_start, match.end() + 600)]
                explicit_method = METHOD_PATTERN.search(tail)
                method = explicit_method.group(1).upper() if explicit_method else "GET"
                line = source.count("\n", 0, match.start()) + 1
                calls.append((path.relative_to(ROOT), line, route, method))
    return calls


def _resolve_local_ref(schema: dict[str, Any], ref: str) -> Any:
    current: Any = schema
    for raw_part in ref.removeprefix("#/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    return current


def test_openapi_schema_is_structurally_linted() -> None:
    schema = app.openapi()
    assert schema["openapi"].startswith("3.1")
    assert schema["info"]["version"]

    operation_ids: list[str] = []
    for path, path_item in schema["paths"].items():
        assert path.startswith("/"), path
        methods = {key for key in path_item if key.lower() in HTTP_METHODS}
        assert methods, f"{path} has no HTTP operations"
        for method in methods:
            operation = path_item[method]
            assert operation.get("responses"), f"{method.upper()} {path} has no responses"
            if operation.get("operationId"):
                operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids)), "OpenAPI operationId values must be unique"
    refs = {
        value
        for node in _walk(schema)
        if isinstance(node, dict)
        for key, value in node.items()
        if key == "$ref" and isinstance(value, str) and value.startswith("#/")
    }
    for ref in refs:
        assert _resolve_local_ref(schema, ref) is not None, ref


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_openapi_path_and_method_surface_matches_reviewed_contract() -> None:
    operations = {path: sorted(methods) for path, methods in sorted(_operations(app.openapi()).items())}
    canonical = json.dumps(operations, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == EXPECTED_PATH_METHOD_SHA256


def test_production_frontend_calls_match_openapi_paths_and_methods() -> None:
    operations = _operations(app.openapi())
    failures: list[str] = []
    for source, line, frontend_path, method in _frontend_calls():
        matches = [path for path in operations if _segments_match(frontend_path, path)]
        if not matches:
            failures.append(f"{source}:{line}: no OpenAPI path matches {frontend_path}")
            continue
        if not any(method in operations[path] for path in matches):
            failures.append(
                f"{source}:{line}: {method} {frontend_path} not present; "
                f"matched {', '.join(f'{path}={sorted(operations[path])}' for path in matches)}"
            )
    assert not failures, "\n" + "\n".join(failures)


def test_api_assembly_and_each_domain_router_stay_bounded() -> None:
    assert len((ROOT / "api" / "main.py").read_text(encoding="utf-8").splitlines()) <= 500
    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in (ROOT / "api" / "routers").glob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 500
    }
    assert oversized == {}
