# Build from this folder:  docker build -t service-template .
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked -p service-template

# Distroless: no shell, no package manager, runs as a non-root user.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/service-template /service
ENV WFK_SERVICE_NAME=service-template
EXPOSE 7020
ENTRYPOINT ["/service"]
