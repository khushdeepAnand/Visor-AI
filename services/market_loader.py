"""Compatibility wrapper for rebuilding the consolidated symbol catalogue."""

from build_market_database import build


def load_all():
    return build()


def load_csv(_filepath):
    """Deprecated compatibility entry point; rebuilds the full catalogue safely."""
    return build()
