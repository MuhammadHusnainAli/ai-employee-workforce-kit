from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "brain-service"
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:5432/brain_db"
