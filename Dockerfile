FROM cgr.dev/chainguard/python:latest-dev AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

FROM cgr.dev/chainguard/python:latest

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY . .

# Detectar versión de Python y crear script de inicio
RUN PYVER=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") && \
    echo "#!/bin/sh" > /app/start.sh && \
    echo "export PYTHONPATH=/app/.venv/lib/python${PYVER}/site-packages" >> /app/start.sh && \
    echo "exec python run.py" >> /app/start.sh && \
    chmod +x /app/start.sh

EXPOSE 8080

USER nonroot

CMD ["/bin/sh", "/app/start.sh"]
