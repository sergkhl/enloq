# The VPS service image (ADR 40). ./scripts/deploy builds it from a committed
# tree; the service folder bind-mounts data, config and secrets at run time.
FROM node:22.23.3-trixie-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
COPY shared/ ../shared/
RUN npm run build

FROM python:3.13.16-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend backend
COPY scripts scripts
RUN uv sync --locked --no-dev
COPY --from=web /src/web/dist web/dist
# The owner's account on the VPS is 1000:1000, so mounted private files keep
# the ownership and modes the private-file checks require.
RUN mkdir -m 700 .local config secrets && chown 1000:1000 .local config secrets
USER 1000:1000
EXPOSE 8765
CMD ["python", "-m", "enloq.cli", "--mode", "personal", "--port", "8765"]
