# Build from this folder:  docker build -t brain-service .
FROM ghcr.io/astral-sh/uv:0.12.20 AS uv

FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# brain-core is Rust (PyO3, built by maturin): the build stage needs a linker and the pinned
# Rust toolchain. Neither reaches the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.99.1
ENV PATH=/root/.cargo/bin:$PATH
# Same path as the runtime image, so the venv's script shebangs stay valid.
WORKDIR /app
COPY . .
# Installs this service (with its copy of the platform code) as a regular, non-editable package.
RUN uv sync --frozen --no-dev --no-editable --package brain-service

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    WFK_SERVICE_NAME=brain-service
USER 10001
EXPOSE 7014 7015
CMD ["brain-service"]
