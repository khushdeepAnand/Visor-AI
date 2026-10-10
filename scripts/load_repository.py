"""Real disposable SQLite repository load; does not claim production/Postgres acceptance."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from services.db.sqlite_impl import SQLiteDatabase, SQLitePredictionDAO


def run(workers: int, operations: int) -> dict[str, object]:
    if not 1 <= workers <= 64 or not 1 <= operations <= 100000:
        raise ValueError("Load limits: 1..64 workers and 1..100000 operations")
    previous = database.DATABASE
    with tempfile.TemporaryDirectory(prefix="stockpilot-repository-load-") as directory:
        database.DATABASE = str(Path(directory) / "load.db")
        try:
            database.create_tables()
            conn = database.get_connection()
            conn.execute("INSERT INTO users(id,name,email,password) VALUES(1,'Load','load@example.invalid','disabled')")
            conn.commit()
            conn.close()
            def write(number: int) -> tuple[int, float]:
                start = time.perf_counter()
                db = SQLiteDatabase()
                try:
                    row_id = SQLitePredictionDAO(db).save_range_forecast(1, f"LOAD{number}", {
                        "generated_at": "2026-10-08T10:00:00+00:00", "forecast": {"low": 90., "median": 100., "high": 110., "confidence_level": .8},
                        "training": {"timeframe": "1D"}, "context": {"provider": "synthetic_load_fixture"}})
                    return row_id, time.perf_counter() - start
                finally:
                    db.close()
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(write, range(operations)))
            elapsed = time.perf_counter() - start
            conn = database.get_connection()
            try:
                rows = conn.execute("SELECT id,symbol,snapshot_hash FROM prediction_history").fetchall()
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            if len(rows) != operations or len({row[0] for row in rows}) != operations or {row[0] for row in rows} != {row[0] for row in results} or integrity != "ok":
                raise RuntimeError("Repository concurrency preservation failed")
            latencies = sorted(item[1] for item in results)
            return {"status": "passed_local_sqlite_repository_load", "backend": "sqlite", "workers": workers,
                    "operations": operations, "persisted_rows": len(rows), "elapsed_seconds": elapsed,
                    "operations_per_second": operations / elapsed, "median_latency_ms": statistics.median(latencies) * 1000,
                    "p95_latency_ms": latencies[int(.95 * (len(latencies) - 1))] * 1000, "integrity": integrity,
                    "scope": "Disposable real SQLite DB, synthetic workload; not API/multi-process/Postgres/Redis or production-shape acceptance"}
        finally:
            database.DATABASE = previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--operations", type=int, default=400)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.workers, args.operations)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{report['status']}: {report['persisted_rows']} preserved rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
