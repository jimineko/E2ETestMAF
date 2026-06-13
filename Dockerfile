FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

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
RUN pip install --no-cache-dir '.[azure-monitor]'

RUN mkdir -p /app/artifacts /app/checkpoints /app/auth

CMD ["python", "-m", "maf_qa"]
