# Build from this folder:  docker build -t sandbox-runner .
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked -p sandbox-runner

# Distroless: no shell, no package manager, runs as a non-root user.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/sandbox-runner /service
ENV WFK_SERVICE_NAME=sandbox-runner
EXPOSE 7015
ENTRYPOINT ["/service"]
