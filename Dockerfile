FROM python:3.12-slim

# Pin the uv version so image builds are reproducible (was :latest).
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY app ./app
COPY scripts ./scripts

RUN uv sync --frozen

# Run as a non-root user, and give it a writable data dir for the SQLite volume.
RUN addgroup --system limon \
    && adduser --system --ingroup limon --no-create-home limon \
    && mkdir -p /app/data \
    && chown limon:limon /app/data

USER limon

EXPOSE 8000

# Cloud Run injects PORT. The fallback keeps local Docker usage on port 8000.
# PATH already points at the venv, so invoke uvicorn directly (no `uv run`),
# and exec so uvicorn is PID 1 and receives Cloud Run's SIGTERM cleanly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
