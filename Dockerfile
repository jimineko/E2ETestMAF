FROM --platform=linux/amd64 python:3.13-slim-bookworm

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

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY agents ./agents
COPY skills ./skills
RUN uv sync --frozen --no-dev --extra azure-monitor --extra hyperlight-runtime

RUN groupadd --gid 10001 mafqa \
    && useradd --uid 10001 --gid mafqa --create-home --shell /usr/sbin/nologin mafqa \
    && mkdir -p /app/artifacts /app/checkpoints /app/auth \
    && chown -R mafqa:mafqa /app/artifacts /app/checkpoints /app/auth

USER mafqa

CMD ["maf-e2e"]
