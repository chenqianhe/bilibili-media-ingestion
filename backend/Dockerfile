# syntax=docker/dockerfile:1

FROM oven/bun:1 AS frontend-build

WORKDIR /app

COPY package.json bun.lock /app/
COPY scripts /app/scripts
COPY frontend/package.json /app/frontend/

WORKDIR /app/frontend

RUN bun install --frozen-lockfile

COPY frontend /app/frontend

ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}

RUN bun run build


FROM python:3.10-slim AS backend-runtime

ENV PYTHONUNBUFFERED=1

ARG APP_VERSION=
ARG APP_BUILD_COMMIT=
ARG APP_BUILD_SHORT_COMMIT=
ARG APP_BUILD_BRANCH=
ARG APP_BUILD_TIME=
ENV APP_VERSION=${APP_VERSION}
ENV APP_BUILD_COMMIT=${APP_BUILD_COMMIT}
ENV APP_BUILD_SHORT_COMMIT=${APP_BUILD_SHORT_COMMIT}
ENV APP_BUILD_BRANCH=${APP_BUILD_BRANCH}
ENV APP_BUILD_TIME=${APP_BUILD_TIME}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package app

COPY ./backend/scripts /app/backend/scripts

COPY ./backend/pyproject.toml ./backend/alembic.ini /app/backend/

COPY ./backend/app /app/backend/app

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --package app

WORKDIR /app/backend/

CMD ["fastapi", "run", "--workers", "4", "app/main.py"]


FROM backend-runtime AS app

COPY --from=frontend-build /app/frontend/dist /app/backend/app/static
