FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev
RUN npx playwright install --with-deps chrome

COPY pyproject.toml README.md ./
COPY src ./src
COPY agents ./agents
COPY skills ./skills
RUN uv sync --no-dev --extra azure-monitor

RUN mkdir -p /app/artifacts /app/checkpoints /app/auth

CMD ["maf-qa"]
