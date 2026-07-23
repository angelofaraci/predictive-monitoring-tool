# Walking-skeleton container image (Phase 2.5).
#
# Single-stage build on the uv-preinstalled image: `uv sync --frozen`
# reproduces the exact locked dependency set from `uv.lock`, then uvicorn
# serves the FastAPI app defined in `src/predictive_monitoring_tool/api/main.py`.
#
# Multi-stage was considered and rejected for this skeleton (marginal size
# win, more lines to maintain) — see sdd/phase2.5-walking-skeleton design.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "predictive_monitoring_tool.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
