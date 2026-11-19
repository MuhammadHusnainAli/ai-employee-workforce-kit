# Build from this folder:  docker build -t core-rust .
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked -p core-rust

# Distroless: no shell, no package manager, runs as a non-root user.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/core-rust /service
EXPOSE 7002
ENTRYPOINT ["/service"]
