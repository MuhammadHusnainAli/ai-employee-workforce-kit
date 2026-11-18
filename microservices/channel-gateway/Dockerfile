# Build from this folder:  docker build -t channel-gateway .
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked -p channel-gateway

# Distroless: no shell, no package manager, runs as a non-root user.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/channel-gateway /service
ENV WFK_SERVICE_NAME=channel-gateway
EXPOSE 7013
ENTRYPOINT ["/service"]
