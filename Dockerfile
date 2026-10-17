FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Where the container writes logs and looks for an optional config.toml
# override. Mount a host folder here.
ENV HL_DATA_DIR=/data

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY config.toml ./
COPY src ./src
COPY scripts ./scripts

# Explicit copies keep .env out of every layer. ClickHouse credentials come
# from a .env file mounted at /app/.env when the container starts.
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "hyperliquid-candles"]
