from __future__ import annotations

from typing import Literal

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "brain-service"
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:5432/brain_db"
    # Role tenant transactions run as, so RLS applies even when the login is a superuser
    # (the local stack logs in as one). Empty in production: the service logs in as it.
    db_tenant_role: str | None = "wfk_app"

    # Query embeddings: "hash" (local, no model) or "gateway" (the Model Gateway).
    embedder: Literal["hash", "gateway"] = "hash"
    model_gateway_url: str = "http://127.0.0.1:4000/v1"
    model_gateway_key: str = "sk-wfk-local"
    embedding_model: str = "embeddings"
