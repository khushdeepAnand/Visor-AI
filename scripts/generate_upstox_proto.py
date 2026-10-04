"""Fetch Upstox's official Market Data Feed V3 proto and generate Python bindings.

Usage:
    python -m pip install -r requirements-dev.txt
    python scripts/generate_upstox_proto.py

The generated module is intentionally not fabricated or hand-maintained. This
script always starts from Upstox's published V3 .proto URL.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests

PROTO_URL = "https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "services" / "market_data" / "streaming" / "upstox_proto"
PROTO = OUT / "MarketDataFeedV3.proto"
GENERATED = OUT / "MarketDataFeedV3_pb2.py"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    response = requests.get(PROTO_URL, timeout=20, headers={"User-Agent": "StockPilotAI/6.0"})
    response.raise_for_status()
    text = response.text
    if "message FeedResponse" not in text or "syntax = \"proto3\"" not in text:
        raise RuntimeError("Downloaded file does not look like the Upstox V3 market-data proto.")
    PROTO.write_text(text, encoding="utf-8")
    command = [
        sys.executable, "-m", "grpc_tools.protoc", f"-I{OUT}", f"--python_out={OUT}", str(PROTO),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError("grpc_tools.protoc failed. Install requirements-dev.txt and retry.")
    # protoc names output after the input file. The adapter expects this exact filename.
    if not GENERATED.exists():
        alternative = OUT / f"{PROTO.stem}_pb2.py"
        if alternative.exists() and alternative != GENERATED:
            alternative.replace(GENERATED)
    if not GENERATED.exists():
        raise RuntimeError(f"Expected generated module was not created: {GENERATED}")
    print(f"Generated {GENERATED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
