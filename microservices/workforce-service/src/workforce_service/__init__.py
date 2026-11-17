"""workforce-service.

Layout (see microservices/README.md):
    api/        transport: gRPC servicers, HTTP routes, request validation
    domain/     business logic and invariants; no I/O
    adapters/   Postgres, NATS, Temporal, other services
"""
