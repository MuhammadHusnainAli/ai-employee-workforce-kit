from __future__ import annotations

from typing import Literal

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "brain-service"
    # One local port, from the 7000-7020 range (deployments/README.md).
    port: int = 7014
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:7000/brain_db"
    # Pinned-context cache (identity and rules), the agent runtime's fallback. Empty: off.
    valkey_url: str = "redis://127.0.0.1:7005/0"

    # Query embeddings: "hash" (local, no model) or "gateway" (the Model Gateway).
    embedder: Literal["hash", "gateway"] = "hash"
    model_gateway_url: str = "http://127.0.0.1:7007/v1"
    model_gateway_key: str = "sk-wfk-local"
    embedding_model: str = "embeddings"

    # The write path's extractor: "baseline" (the episode only, no model) or "gateway" (the
    # episode plus facts, preferences and lessons from `extraction_model`).
    extractor: Literal["baseline", "gateway"] = "baseline"
    extraction_model: str = "claude-haiku"
    # What happens to personal data in memories when a tenant has not set its own policy.
    pii_policy: Literal["keep", "redact", "drop"] = "redact"
    # Nightly consolidation (brain-ingest creates the Temporal schedule; cron, UTC).
    consolidation_cron: str = "0 2 * * *"
