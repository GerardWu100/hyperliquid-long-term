FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY config.toml ./
COPY src ./src
COPY scripts ./scripts

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "hl-ingest"]
