from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "service-template"
    # One local port, from the 7000-7020 range (deployments/README.md). Give a new service its
    # own free port: two services on the same port cannot run side by side.
    port: int = 7020
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:7000/service_template_db"
