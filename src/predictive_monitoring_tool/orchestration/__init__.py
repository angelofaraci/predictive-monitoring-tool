"""In-process real-mode polling orchestration (spec: fase-7-orquestacion.md).

Closes the automatic loop in real mode: poll Prometheus -> ingest -> on a
new anomaly, diagnose it with the Phase 6 agent in the background -> all
persisted, with no manual intervention. Demo mode is untouched — it stays
triggered on demand from the UI (Phase 8).
"""

from __future__ import annotations
