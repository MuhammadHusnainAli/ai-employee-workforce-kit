# Build from this folder:  docker build -t evaluation-service .
FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Same path as the runtime image, so the venv's script shebangs stay valid.
WORKDIR /app
COPY . .
# Installs this service (with its copy of the platform code) as a regular, non-editable package.
RUN uv sync --frozen --no-dev --no-editable --package evaluation-service

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
# For `wfk-migrate up --dir /app/migrations` (compose runs it before the service starts).
COPY migrations /app/migrations
# The golden, regression and safety suites.
COPY suites /app/suites
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    WFK_SERVICE_NAME=evaluation-service \
    WFK_SUITES_DIR=/app/suites
USER 10001
CMD ["evaluation-service"]
