"""Bounded, sanitized store of recent server-side failures.

The store exists so an administrator can see *that* something is failing and how
often, without any exception text reaching a client. Only the exception class
name, a stable fingerprint, a scope and counts are retained: never the exception
message, never a traceback, never a request body, never a value that a provider
or database driver might have embedded in an error string.
"""
from __future__ import annotations

import hashlib
import threading
import traceback
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

#: Maximum distinct fingerprints retained. Oldest-seen groups are evicted first,
#: which bounds memory regardless of how long the process runs.
MAX_GROUPS = 200

_LOCK = threading.Lock()
_GROUPS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _fingerprint(scope: str, exc: BaseException) -> str:
    """Group failures by scope, exception type and originating code location.

    The location comes from the traceback frames rather than the message, so two
    failures differing only in an embedded symbol or credential still collapse
    into one group and nothing sensitive contributes to the identity.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    tail = frames[-1] if frames else None
    where = f"{tail.filename}:{tail.lineno}" if tail is not None else "unknown"
    seed = f"{scope}|{type(exc).__name__}|{where}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def record_error(scope: str, support_id: str, exc: BaseException) -> str:
    """Register one failure and return its fingerprint."""
    fingerprint = _fingerprint(scope, exc)
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        group = _GROUPS.get(fingerprint)
        if group is None:
            if len(_GROUPS) >= MAX_GROUPS:
                _GROUPS.popitem(last=False)
            group = {
                "fingerprint": fingerprint,
                "scope": scope,
                "exception_type": type(exc).__name__,
                "count": 0,
                "first_seen": now,
                "last_seen": now,
                "recent_support_ids": [],
            }
            _GROUPS[fingerprint] = group
        group["count"] += 1
        group["last_seen"] = now
        # A short tail of support ids lets an administrator match a user's
        # reported id to a group. The ids are opaque and carry no user data.
        group["recent_support_ids"] = ([support_id] + list(group["recent_support_ids"]))[:10]
        _GROUPS.move_to_end(fingerprint)
    return fingerprint


def error_groups(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        groups = [dict(group) for group in _GROUPS.values()]
    groups.sort(key=lambda group: (group["last_seen"], group["count"]), reverse=True)
    return groups[: max(1, min(int(limit), MAX_GROUPS))]


def error_summary() -> dict[str, Any]:
    with _LOCK:
        total = sum(group["count"] for group in _GROUPS.values())
        distinct = len(_GROUPS)
    return {"total_failures": total, "distinct_groups": distinct, "retained_groups_max": MAX_GROUPS}


def reset() -> None:
    """Clear the store. Used by tests and by the safe maintenance action."""
    with _LOCK:
        _GROUPS.clear()
