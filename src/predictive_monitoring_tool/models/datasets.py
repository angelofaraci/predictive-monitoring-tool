"""Training and evaluation dataset assembly for the anomaly detector.

Pipeline: `data.generator.generate()` -> `data.features.build_features()` ->
this module's pinned constants decide seeds, durations, and scenario mix.
`data/` stays pure generation+features and is not modified here — seeds,
the train/eval split, and the scenario mix are training concerns that live
in `models/`, not `data/` (see design decision: dataset assembly lives in
`models/datasets.py`).

`build_evaluation_dataset` builds each 1-day segment's features BEFORE
concatenation (per-segment `build_features()`), so rolling/lag windows
never bleed across segment boundaries. Each segment is anchored at
`FIXED_START_ANCHOR + i days` so the concatenated index stays unique and
monotonic increasing.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from predictive_monitoring_tool.data.features import build_features
from predictive_monitoring_tool.data.generator import FIXED_START_ANCHOR, generate

INTERVAL_SECONDS = 60
TRAIN_SEED = 42
TRAIN_DURATION_MINUTES = 7 * 24 * 60
EVAL_DURATION_MINUTES = 24 * 60
SCENARIO_START_MINUTE = 480

EVAL_SEGMENTS: tuple[tuple[str, int], ...] = (
    ("memory_leak", 1001),
    ("cpu_spike", 1002),
    ("disk_fill", 1003),
    ("service_down", 1004),
)

LABEL_COLUMNS: tuple[str, ...] = ("is_anomaly", "scenario")


def build_training_dataset(
    *,
    duration_minutes: int = TRAIN_DURATION_MINUTES,
    interval_seconds: int = INTERVAL_SECONDS,
    seed: int = TRAIN_SEED,
) -> pd.DataFrame:
    """Assemble the normal-only training dataset.

    Calls `generate(seed=seed)` for `duration_minutes` with no scenario,
    then applies `build_features()`. Deterministic and byte-reproducible
    for a fixed seed (see spec: Training Dataset Assembly).
    """
    raw = generate(
        duration_minutes=duration_minutes,
        interval_seconds=interval_seconds,
        seed=seed,
    )
    return build_features(raw)


def build_evaluation_dataset(
    *,
    segments: Sequence[tuple[str, int]] = EVAL_SEGMENTS,
    duration_minutes: int = EVAL_DURATION_MINUTES,
    interval_seconds: int = INTERVAL_SECONDS,
    scenario_start_minute: int = SCENARIO_START_MINUTE,
) -> pd.DataFrame:
    """Assemble the fixed multi-scenario evaluation dataset.

    Builds one `build_features()`-processed segment per `(scenario, seed)`
    pair in `segments`, each anchored at `FIXED_START_ANCHOR + i days`
    (`i` = segment index) so windows never bleed across segment boundaries
    and the concatenated index is unique and monotonic increasing.
    """
    frames = []
    for i, (scenario_name, seed) in enumerate(segments):
        start_time = FIXED_START_ANCHOR + pd.Timedelta(days=i)
        raw = generate(
            duration_minutes=duration_minutes,
            interval_seconds=interval_seconds,
            scenario=scenario_name,
            scenario_start_minute=scenario_start_minute,
            start_time=start_time,
            seed=seed,
        )
        frames.append(build_features(raw))
    return pd.concat(frames)


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Numeric/bool columns of `df`, excluding `LABEL_COLUMNS`.

    Preserves `df`'s own column order (metadata contract: this order is
    persisted and used to rebuild the feature vector at inference time).
    """
    numeric_bool = set(df.select_dtypes(include=["number", "bool"]).columns)
    return [c for c in df.columns if c in numeric_bool and c not in LABEL_COLUMNS]


def feature_matrix(df: pd.DataFrame, columns: Sequence[str]) -> np.ndarray:
    """The only DataFrame -> array boundary: `df[columns]` as float64."""
    return df[list(columns)].to_numpy(dtype="float64")
