FROM cgr.dev/chainguard/python:latest-dev AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# Runtime chainguard: distroless, SIN shell. Nada de RUN/CMD con /bin/sh:
# entrypoint exec directo al python del venv (resuelve sus site-packages solo).
FROM cgr.dev/chainguard/python:latest

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY run.py .

USER nonroot

EXPOSE 8080

ENTRYPOINT ["/app/.venv/bin/python", "run.py"]
