"""Service settings read from the environment (prefix WFK_)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Settings every service has. Services subclass this to add their own.

    Defaults point at the local stack in deployments/docker-compose.yml.
    """

    model_config = SettingsConfigDict(env_prefix="WFK_", env_file=None, extra="ignore")

    service_name: str = Field(min_length=1)
    environment: Literal["local", "test", "staging", "production"] = "local"
    cell_id: str = "cell-local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    http_host: str = "0.0.0.0"  # noqa: S104 - services listen on all interfaces inside their pod
    http_port: int = Field(default=8080, ge=1, le=65535)
    grpc_port: int = Field(default=50051, ge=1, le=65535)

    database_url: str = "postgresql://wfk:wfk@127.0.0.1:5432/wfk"
    nats_url: str = "nats://127.0.0.1:4222"
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"

    # Empty disables export (spans and metrics are still created, just not sent).
    otlp_endpoint: str = ""

    # Deadline for each readiness check, in seconds.
    readiness_timeout_s: float = Field(default=2.0, gt=0)
