# Spec — Phase 2: Feature pipeline with pandas

## 1. Phase objective

Transform the raw metrics produced by `generate()` (Phase 1) into a feature dataset ready to train the anomaly detection model in Phase 3. Phase 2 is purely about data: it does not touch the model, the API, the agent, or deploy.

## 2. Prerequisite — small adjustment to Phase 1

To be able to test that the feature pipeline "reacts" correctly during an anomaly, and to be able to evaluate the model later on, the generator needs to expose ground truth. If `generate()` doesn't already do this, add the following before continuing:

- `is_anomaly` (bool): `True` on rows within the injected anomaly window, `False` otherwise.
- `scenario` (str | None): name of the scenario active on that row (`"memory_leak"`, `"cpu_spike"`, etc.), `None` if there is no anomaly.

This is a minor change to `generator.py`; there's no need to reopen all of Phase 1.

## 3. Functional scope

### 3.1 Rolling window features

For each raw metric (`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`), compute over at least two windows (e.g., 5min and 15min):

- rolling mean
- rolling standard deviation

### 3.2 Lag features

Value of each metric at `t-1` and `t-5` (in terms of rows/samples, not wall-clock time).

### 3.3 Variation features

First-order difference (`diff`) per metric — captures the rate of change, useful for detecting sharp spikes vs. gradual increases.

### 3.4 Temporal features

Derived from the timestamp: `hour`, `day_of_week`, `is_business_hours` (bool). These let the model distinguish "it's nighttime, low traffic is normal" from "low traffic mid-morning is unusual."

### 3.5 NaN handling

Rolling windows and lags produce NaNs in the first rows (warm-up). Policy: drop these rows instead of filling them — inventing placeholder values could be confused with real data during model training.

### 3.6 Expected interface

```python
def build_features(
    df: pandas.DataFrame,
    windows: list[str] = ["5min", "15min"],
) -> pandas.DataFrame:
    ...
```

Receives the raw DataFrame from `generate()` (with `is_anomaly`/`scenario` if the prerequisite has already been added).

Returns a DataFrame with the original columns plus all new features, with no NaNs, and propagating `is_anomaly`/`scenario` unmodified (the Phase 3 model will need them for evaluation, even though Isolation Forest is unsupervised).

## 4. New file structure

```
src/predictive_monitoring_tool/
└── data/
    ├── generator.py      # phase 1
    ├── scenarios.py       # phase 1
    └── features.py        # ← new, phase 2
tests/
└── test_features.py       # ← new
notebooks/
└── 02_feature_engineering.ipynb   # ← new
```

## 5. Definition of Done

- [ ] Prerequisite from section 2 resolved (`is_anomaly`/`scenario` in `generate()`)
- [ ] `features.py` implements `build_features()` with rolling windows, lags, diff, and temporal features
- [ ] NaN policy applied (drop incomplete rows) and documented in a docstring
- [ ] Unit tests:
  - expected shape/columns in the output
  - no NaNs remain in the result
  - during a `memory_leak` window, the rolling mean of `memory_pct` is increasing (validates that the feature actually captures the signal)
- [ ] Notebook 02 plots, for at least one scenario, the raw metric vs. its rolling mean, marking the anomaly window
- [ ] README updated with a short "Feature engineering" section

## 6. Out of scope for this phase

Do not touch: the ML model (Phase 3), FastAPI, the agent, MCP, deploy. If there's a temptation to already try a model with these features, note it as a next step but don't implement it yet.

## 7. Notes for the agent

- Keep `build_features()` as a pure function (does not read or write files) so Phase 3 can import it and chain it directly after `generate()`.
- Use vectorized pandas operations (`.rolling()`, `.shift()`, `.diff()`) — no row-by-row loops, both for performance and because it's idiomatic in pandas.
- Same conventions as in Phase 1: type hints, docstrings in English, Python 3.14, `scikit-learn>=1.9.0` already pinned even though this phase doesn't use it yet.
