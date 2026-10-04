# Upstox V3 protobuf decoder

StockPilot refuses to guess broker protobuf field numbers. The native stream is enabled only when a real Upstox token and a generated `MarketDataFeedV3_pb2.py` are both present.

Recommended setup from the repository root:

```powershell
python -m pip install -r requirements-dev.txt
python scripts/generate_upstox_proto.py
```

The generator downloads the current official Upstox V3 schema from:
`https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`

After generation, restart the backend. `UpstoxStreamAdapter.is_configured()` will become `True` only if `UPSTOX_ACCESS_TOKEN` (or `UPSTOX_ANALYTICS_TOKEN`) is also configured.

Do not commit real tokens or captured account data. Sanitized fixtures may be committed after live verification.
