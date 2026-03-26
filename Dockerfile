FROM python:3.11-slim

WORKDIR /app

# Install uv from official image — no curl, no PATH hacks
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# ── Dependency layer (cached unless pyproject.toml or uv.lock changes) ───────
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen

# ── Application code ──────────────────────────────────────────────────────────
COPY backend/ ./backend/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
