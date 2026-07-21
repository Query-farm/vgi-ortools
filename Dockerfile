# Copyright 2026 Query Farm LLC - https://query.farm
#
# Single image serving BOTH transports of the vgi-ortools worker:
#   docker run ... IMG            -> HTTP server on $PORT (default 8000; /health, VGI RPC)
#   docker run -i ... IMG stdio   -> stdio worker DuckDB spawns on-host
# See docker-entrypoint.sh. Prescriptive compute (OR-Tools CP-SAT / routing / flow /
# packing) runs purely local — no network, no state, no volume.
# syntax=docker/dockerfile:1
FROM python:3.13-slim

ARG VERSION=0.0.0
ARG GIT_COMMIT=unknown
ARG SOURCE_URL=https://github.com/Query-farm/vgi-ortools

LABEL org.opencontainers.image.title="vgi-ortools" \
      org.opencontainers.image.description="Google OR-Tools optimization SQL functions for DuckDB via VGI (stdio + HTTP)" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.licenses="MIT" \
      farm.query.vgi.transports='["http","stdio"]'

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    VGI_ORTOOLS_GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

# curl backs the HEALTHCHECK and the CI /health smoke.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install the worker + HTTP-serving extra from the source tree.
COPY pyproject.toml README.md LICENSE ./
COPY vgi_ortools ./vgi_ortools
RUN pip install '.[serve]'

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=8s \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["http"]
