"""Model artifact persistence: joblib binary + companion metadata JSON.

Both files are gitignored (`models/*.joblib`, `models/*.metadata.json`) and
regenerable via the CI-runnable training entrypoint (`models/train.py`,
PR2). The metadata JSON is the Phase 4 contract: it carries the exact
feature column order so downstream code can rebuild the feature vector in
the same order the model was fit on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import joblib
import sklearn

MODEL_DIR = Path("models")
MODEL_VERSION = "v1"
MODEL_FILENAME = "isolation_forest_v1.joblib"
METADATA_FILENAME = "isolation_forest_v1.metadata.json"


def save_model(
    result: Any,
    metrics: Mapping[str, Any],
    latency: Mapping[str, float],
    *,
    directory: Path = MODEL_DIR,
) -> tuple[Path, Path]:
    """Persist `result.model` via joblib plus a companion metadata JSON.

    `result` MUST expose `.model`, `.feature_columns`, `.hyperparameters`,
    `.trained_at` (the `TrainingResult` contract from `models/train.py`,
    PR2). Metadata schema (Phase 4 contract): `model_version`,
    `sklearn_version`, `trained_at`, `hyperparameters`, `feature_columns`
    (ordered), `metrics`, `latency_ms`.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    joblib_path = directory / MODEL_FILENAME
    metadata_path = directory / METADATA_FILENAME

    joblib.dump(result.model, joblib_path)

    metadata = {
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "trained_at": result.trained_at,
        "hyperparameters": dict(result.hyperparameters),
        "feature_columns": list(result.feature_columns),
        "metrics": metrics,
        "latency_ms": dict(latency),
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return joblib_path, metadata_path


def load_model(directory: Path = MODEL_DIR) -> tuple[Any, dict]:
    """Load the joblib model and its metadata dict from `directory`.

    Raises `FileNotFoundError` if either file is missing.
    """
    directory = Path(directory)
    joblib_path = directory / MODEL_FILENAME
    metadata_path = directory / METADATA_FILENAME

    if not joblib_path.exists():
        raise FileNotFoundError(f"Model artifact not found: {joblib_path}")
    if not metadata_path.exists():
        raise FileNotFoundError(f"Model metadata not found: {metadata_path}")

    model = joblib.load(joblib_path)
    with open(metadata_path) as f:
        metadata = json.load(f)

    return model, metadata
