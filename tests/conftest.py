"""Session-scoped fixtures for the models test suite.

Fixtures build the training/eval datasets and fit the model in-process at
the FULL pinned production configuration (never a shrunk config, never
reading from `models/` on disk) — see design decision: "tests train
in-process; never read `models/`". Session scope amortizes the fit
(~10,075 training rows, `max_samples=256`, `n_jobs=1` -> low single-digit
seconds) across both `test_train.py` and `test_evaluate.py`.
"""

from __future__ import annotations

import pytest

from predictive_monitoring_tool.models.datasets import (
    build_evaluation_dataset,
    build_training_dataset,
)
from predictive_monitoring_tool.models.train import TrainingResult, train_model


@pytest.fixture(scope="session")
def training_dataset():
    """Full pinned training dataset (7 days, seed=42, no scenario)."""
    return build_training_dataset()


@pytest.fixture(scope="session")
def evaluation_dataset():
    """Full pinned evaluation dataset (4 scenario segments, seeds 1001-1004)."""
    return build_evaluation_dataset()


@pytest.fixture(scope="session")
def trained_model(training_dataset) -> TrainingResult:
    """`IsolationForest` fitted on `training_dataset` at pinned hyperparameters."""
    return train_model(training_dataset)
