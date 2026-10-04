"""Structural proof that no broker order mutation path exists in the product.

`REGULATORY_REVIEW_REQUIRED.md` makes one boundary non-negotiable while review is
outstanding: every order in StockPilot is simulated, and the application must not
be able to place, modify, or cancel a real broker order. Disclaimers alone cannot
enforce that, so this test scans the shipped source for the shapes such a feature
would take.

The test is deliberately static: it does not import the FastAPI app, so it runs in
any environment, and it fails if someone later adds a live trading call even if
that call is never exercised by another test.

The scan covers shipped application code only. The test suite itself is excluded
because `tests/test_phase4_security.py` deliberately contains the blocked broker
order endpoints as fixtures for its allowlist assertions, and an allowlist test
naming a forbidden URL is evidence of the boundary rather than a breach of it.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Directories that never contain shipped application logic.
SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".next",
    "logs",
    "cache",
    "models",
    "database",
    ".pytest_cache",
    ".venv",
    "venv",
    # Test suites assert about forbidden broker endpoints by naming them.
    "tests",
}

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}

#: Broker REST endpoints that mutate real orders, by vendor documentation.
#: A match means the code is talking to a live order book, not a simulation.
LIVE_ORDER_ENDPOINTS = (
    re.compile(r"api\.kite\.trade/orders", re.IGNORECASE),
    re.compile(r"api\.upstox\.com/[^\"']*order", re.IGNORECASE),
    re.compile(r"api\.fyers\.in/[^\"']*order", re.IGNORECASE),
)

#: Client-library call shapes for placing or amending a live order.
LIVE_ORDER_CALLS = (
    re.compile(r"\bkite\.place_order\b"),
    re.compile(r"\bkite\.modify_order\b"),
    re.compile(r"\bkite\.cancel_order\b"),
    re.compile(r"\bplace_order\s*\("),
    re.compile(r"\bplaceOrder\s*\("),
    re.compile(r"\bmodify_order\s*\("),
    re.compile(r"\bcancel_order\s*\("),
    re.compile(r"\bsquare_off\s*\("),
    re.compile(r"\bexit_position\s*\("),
)

#: Paper-trading routes are the only order surfaces that may exist.
ALLOWED_ORDER_ROUTES = (
    "/api/v1/paper/orders",
    "/api/v1/paper/orders/process",
    "/api/v1/paper/orders/{order_id}",
)

#: Matches `orders` only as a complete path segment, so administrative routes
#: such as `/api/v1/admin/diagnostics/providers/order` (provider *ordering*, not
#: order placement) are not mistaken for a trading surface.
ORDER_ROUTE_PATTERN = re.compile(r"[\"'](/api/v1/[^\"']*/orders(?:/[^\"']*)?)[\"']")


def _source_files() -> list[Path]:
    files: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(PROJECT_ROOT).parts):
            continue
        files.append(path)
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_source_tree_is_scannable() -> None:
    """Guard the guard: an empty scan would make the other tests vacuous."""
    files = _source_files()
    assert len(files) > 50, f"expected to scan the application source, found {len(files)} files"


def test_no_live_broker_order_endpoints() -> None:
    offenders: list[str] = []
    for path in _source_files():
        if path.name == Path(__file__).name:
            continue
        text = _read(path)
        for pattern in LIVE_ORDER_ENDPOINTS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {pattern.pattern}")
    assert not offenders, (
        "Live broker order endpoints must not be reachable while regulatory review is "
        f"outstanding: {offenders}"
    )


def test_no_live_broker_order_calls() -> None:
    offenders: list[str] = []
    for path in _source_files():
        if path.name == Path(__file__).name:
            continue
        text = _read(path)
        for pattern in LIVE_ORDER_CALLS:
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.start())
                line = text[line_start : line_end if line_end != -1 else len(text)]
                # Simulated fills inside the paper-trading engine are allowed, but they
                # must never be routed to a broker client.
                if "paper" in str(path).lower() or "paper" in line.lower():
                    continue
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {line.strip()[:120]}")
    assert not offenders, f"Order mutation calls found outside the paper-trading simulator: {offenders}"


def test_only_paper_order_routes_exist() -> None:
    """Every order route the API exposes must be an explicitly simulated one."""
    found: set[str] = set()
    for path in _source_files():
        if path.name == Path(__file__).name:
            continue
        for match in ORDER_ROUTE_PATTERN.finditer(_read(path)):
            found.add(match.group(1))
    # Guard the guard: if the pattern stops matching, the assertion below would
    # pass vacuously while a live route sat in the source.
    assert "/api/v1/paper/orders" in found, f"paper order route not detected; found {sorted(found)}"
    unexpected = sorted(route for route in found if route not in ALLOWED_ORDER_ROUTES)
    assert not unexpected, f"Unexpected order routes: {unexpected}"


def test_regulatory_boundary_is_still_documented() -> None:
    """The simulation-only boundary must remain stated, not silently dropped."""
    text = _read(PROJECT_ROOT / "REGULATORY_REVIEW_REQUIRED.md").lower()
    assert "keep all orders simulated" in text
    assert "broker order placement" in text
