# Build from this folder:  docker build -t task-service .
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY . .
RUN cargo build --release --locked -p task-service

# Distroless: no shell, no package manager, runs as a non-root user.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/task-service /service
ENV WFK_SERVICE_NAME=task-service
EXPOSE 7016
ENTRYPOINT ["/service"]
