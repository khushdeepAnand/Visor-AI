"""Persist trained research estimators in MLflow; production remains receipt-gated."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


def register_candidate(model: Any, example: pd.DataFrame, *, uri: str, symbol: str, candidate: str,
                       evidence_sha256: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        raise ValueError("Registry requires the verified input-manifest SHA256")
    import mlflow
    import mlflow.sklearn
    from mlflow.models import infer_signature
    from sklearn.ensemble import GradientBoostingRegressor

    if not isinstance(model, GradientBoostingRegressor):
        raise ValueError("This registry path accepts only locally fitted specialist GBM estimators")

    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    experiment = "stockpilot-next-day-research"
    if uri.startswith("sqlite:") and mlflow.get_experiment_by_name(experiment) is None:
        artifacts = Path(__file__).resolve().parents[1] / "local-history" / "mlflow-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        mlflow.create_experiment(experiment, artifact_location=artifacts.as_uri())
    mlflow.set_experiment(experiment)
    name = "stockpilot-next-day-" + re.sub(r"[^A-Za-z0-9_-]", "-", symbol) + "-" + candidate
    with mlflow.start_run(tags={"stockpilot.scope": "research_only", "stockpilot.evidence_sha256": evidence_sha256,
                                "stockpilot.symbol": symbol, "stockpilot.candidate": candidate,
                                "stockpilot.production_authorized": "false"}) as run:
        mlflow.log_params({"horizon_sessions": 1, "training_rows": len(example), "candidate": candidate})
        sample = example.head(5)
        info = mlflow.sklearn.log_model(model, name="model", registered_model_name=name,
                                       signature=infer_signature(sample, model.predict(sample)),
                                       input_example=sample, skops_trusted_types=["sklearn.tree._tree.Tree"],
                                       pip_requirements=["scikit-learn==1.9.0", "pandas==3.0.5", "numpy==2.4.6"])
        return {"run_id": run.info.run_id, "model_uri": info.model_uri, "registered_model": name,
                "version": str(info.registered_model_version), "scope": "research_only"}
