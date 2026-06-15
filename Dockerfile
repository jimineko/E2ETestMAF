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
RUN npx playwright install --with-deps chrome chromium

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY agents ./agents
COPY skills ./skills
RUN uv sync --frozen --no-dev --extra azure-monitor --extra hyperlight-runtime

RUN groupadd --gid 10001 mafe2e \
    && useradd --uid 10001 --gid mafe2e --create-home --shell /usr/sbin/nologin mafe2e \
    && mkdir -p /app/artifacts /app/checkpoints /app/auth \
    && chown -R mafe2e:mafe2e /app/artifacts /app/checkpoints /app/auth

USER mafe2e

CMD ["maf-e2e"]
