"""Minimal FastAPI app proving the container runs correctly once deployed.

Phase 2.5 walking skeleton: the only route is `/health`, a liveness check
used both by tests (`tests/test_health.py`) and by the deploy pipeline's
post-deploy verification step (`.github/workflows/deploy.yml`).
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="predictive-monitoring-tool")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check (spec: GET /health Contract)."""
    return {"status": "ok"}
