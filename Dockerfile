# syntax=docker/dockerfile:1

# Build stage: resolve and install dependencies into a self-contained virtualenv.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies are installed before the source is copied, so editing code does not
# invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Runtime stage: just Python, the virtualenv and the app.
FROM python:3.14-slim

# UID 1000 so the bind-mounted ./data is writable without a chown on the host. A
# mismatch here surfaces as "attempt to write a readonly database" at the first write.
RUN groupadd --gid 1000 fiesta \
    && useradd --uid 1000 --gid 1000 --create-home fiesta

WORKDIR /app

COPY --from=builder --chown=fiesta:fiesta /app/.venv /app/.venv
COPY --chown=fiesta:fiesta src/ ./src/
COPY --chown=fiesta:fiesta migrations/ ./migrations/
COPY --chown=fiesta:fiesta alembic.ini pyproject.toml ./

# The commit this image was built from, reported to Sentry as the release. There
# is no .git inside the image to ask, so pass it: --build-arg IMAGE_TAG=<sha>.
ARG IMAGE_TAG=""

ENV PATH="/app/.venv/bin:$PATH" \
    SENTRY_RELEASE=$IMAGE_TAG \
    PYTHONUNBUFFERED=1 \
    FIESTA_DB_PATH=/app/data/fiesta.db

USER fiesta

# Socket Mode dials out to Slack, so nothing listens and no port is exposed.
# Exec form, so fiesta is PID 1 and gets docker stop's SIGTERM; a `sh -c` wrapper
# would swallow it. Migrations are not run here: run `fiesta migrate` in a
# throwaway container before starting this one.
CMD ["fiesta", "run"]
