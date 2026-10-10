"""Reload registered research models and execute inference; no promotion alias."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def verify(uri: str) -> dict[str, Any]:
    import mlflow
    from mlflow import MlflowClient
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    models = client.search_registered_models(filter_string="name LIKE 'stockpilot-next-day-%'")
    checks = []
    for model in models:
        if model.aliases:
            raise ValueError("Research verification expects no deployment aliases")
        for version in client.search_model_versions(f"name='{model.name}'"):
            run = client.get_run(version.run_id)
            if run.data.tags.get("stockpilot.scope") != "research_only" or run.data.tags.get("stockpilot.production_authorized") != "false":
                raise ValueError("Registered artifact lacks research-only lineage")
            loaded = mlflow.pyfunc.load_model(f"models:/{model.name}/{version.version}")
            columns = [field.name for field in loaded.metadata.get_input_schema().inputs]
            prediction = loaded.predict(pd.DataFrame({name: [0.] for name in columns}))
            if not np.isfinite(prediction).all():
                raise ValueError("Reloaded model failed finite inference")
            checks.append({"name": model.name, "version": version.version, "run_id": version.run_id,
                           "input_manifest_sha256": run.data.tags["stockpilot.evidence_sha256"], "reload_inference": "passed"})
    if not checks:
        raise ValueError("No specialist models registered")
    return {"status": "passed_real_local_registry_reload", "models": checks,
            "scope": "Actual trained weights reloaded from MLflow; zero-valued inference is an artifact smoke check, not market evidence or promotion"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.uri)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Registry reload verified: {len(report['models'])} model versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
