from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "agent-runtime"
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:5432/run_db"

    # The Model Gateway (LiteLLM): the only path to any LLM.
    model_gateway_url: str = "http://127.0.0.1:4000/v1"
    model_gateway_key: str = "sk-wfk-local"

    # Claim-check storage for large workflow payloads (SeaweedFS locally).
    s3_endpoint: str = "http://127.0.0.1:8333"
    s3_access_key: str = "wfk-local-access"
    s3_secret_key: str = "wfk-local-secret"  # noqa: S105 - local stack only; set WFK_S3_SECRET_KEY
    payload_bucket: str = "wfk-payloads"
    claim_check_threshold_bytes: int = 128 * 1024
