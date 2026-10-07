"""Inspect DAO API parity without constructing factories or opening connections.

Checks public methods/properties, parameter names/kinds/defaults/annotations and
return annotations. Backend-specific constructors and private helpers are not a
shared API. Driver return types declared Any in DatabaseInterface and the factory
db property's concrete wrapper types are deliberately normalized to that contract.
This is signature evidence, not SQL correctness or behavioral-parity evidence.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db.base import DatabaseInterface


def _surface(cls: type) -> dict[str, Any]:
    return {name: member for name, member in inspect.getmembers(cls)
            if not name.startswith("_") and (inspect.isfunction(member) or isinstance(member, property))}


def _signature(member: Any, contract: Any = None) -> inspect.Signature:
    function = member.fget if isinstance(member, property) else member
    if function is None:
        raise ValueError("DAO properties must have a readable getter")
    signature = inspect.signature(function, eval_str=True)
    annotation = signature.return_annotation
    if contract is not None and inspect.signature(contract, eval_str=True).return_annotation is Any:
        annotation = Any  # DB-API connections/cursors differ by driver, by design.
    elif isinstance(annotation, type) and issubclass(annotation, DatabaseInterface):
        annotation = DatabaseInterface  # factory.db is the corresponding wrapper.
    return signature.replace(return_annotation=annotation)


def compare_classes(left: type, right: type, *, interface: type | None = None) -> list[str]:
    issues = []
    first, second = _surface(left), _surface(right)
    for name in sorted(first.keys() | second.keys()):
        label = f"{left.__name__}/{right.__name__}.{name}"
        if name not in first or name not in second:
            issues.append(f"{label}: missing from {left.__name__ if name not in first else right.__name__}")
            continue
        if getattr(first[name], "__isabstractmethod__", False) or getattr(second[name], "__isabstractmethod__", False):
            issues.append(f"{label}: inherited abstract method is not an implementation")
            continue
        if isinstance(first[name], property) != isinstance(second[name], property):
            issues.append(f"{label}: property/method mismatch")
            continue
        contract = getattr(interface, name, None) if interface else None
        expected, actual = _signature(first[name], contract), _signature(second[name], contract)
        if expected != actual:
            issues.append(f"{label}: signature mismatch: {expected} != {actual}")
    return issues


def check_parity(sqlite_module: ModuleType | None = None, postgres_module: ModuleType | None = None) -> list[str]:
    if sqlite_module is None or postgres_module is None:
        from services.db import sqlite_impl, postgres_impl
        sqlite_module = sqlite_module or sqlite_impl
        postgres_module = postgres_module or postgres_impl
    left = {name.removeprefix("SQLite"): cls for name, cls in inspect.getmembers(sqlite_module, inspect.isclass)
            if name.startswith("SQLite") and cls.__module__ == sqlite_module.__name__}
    right = {name.removeprefix("Postgres"): cls for name, cls in inspect.getmembers(postgres_module, inspect.isclass)
             if name.startswith("Postgres") and cls.__module__ == postgres_module.__name__}
    issues = []
    if not left or not right:
        issues.append("No concrete backend classes discovered")
    for name in sorted(left.keys() | right.keys()):
        if name not in left or name not in right:
            issues.append(f"{name}: missing backend class")
            continue
        issues.extend(compare_classes(left[name], right[name], interface=DatabaseInterface if name == "Database" else None))
    return issues


def main() -> int:
    issues = check_parity()
    if issues:
        print("Backend signature parity FAILED (non-connecting):")
        for issue in issues:
            print(f"  {issue}")
        return 1
    print("Backend signature parity passed (non-connecting).")
    print("Constructors/private helpers and contract-specific driver return types differ by design.")
    print("Behavioral gaps remain documented in DEPLOYMENT.md; signature parity is not switch approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
