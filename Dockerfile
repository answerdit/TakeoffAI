FROM python:3.11-slim

WORKDIR /app

# git is required by HarnessEvolver to commit evolved personality prompts
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# ── Dependency layer (cached unless pyproject.toml or uv.lock changes) ───────
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen

# ── Application code ──────────────────────────────────────────────────────────
COPY backend/ ./backend/

# ── Git repo so HarnessEvolver can commit evolved prompts ─────────────────────
RUN git config --global user.email "harness@takeoffai.local" \
    && git config --global user.name "TakeoffAI Harness" \
    && git init && git add -A && git commit -m "initial: base harness"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
