# Build from this folder:  docker build -t agent-runtime .
FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Same path as the runtime image, so the venv's script shebangs stay valid.
WORKDIR /app
COPY . .
# Installs this service (with its copy of the platform code) as a regular, non-editable package.
RUN uv sync --frozen --no-dev --no-editable --package agent-runtime

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    WFK_SERVICE_NAME=agent-runtime
USER 10001
EXPOSE 7012
CMD ["agent-runtime"]
