"""Root entry point for the packaged StockPilot AI release.

The FastAPI application lives in :mod:`api.main`.  Packaged releases are
verified with ``python -c "import main"`` from the archive root, so this
module re-exports the real application object rather than defining a second
one.  Importing it performs the same construction as ``import api.main``.
"""
from __future__ import annotations

from api.main import app

__all__ = ["app"]


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)