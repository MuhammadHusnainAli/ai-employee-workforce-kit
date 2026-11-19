from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """The collaboration module's settings (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "collaboration"
    port: int = 7001
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:7017/collab_db"

    # The Model Gateway (LiteLLM): colleagues answer calls and write standup updates through it.
    model_gateway_url: str = "http://127.0.0.1:7024/v1"
    model_gateway_key: str = "sk-wfk-local"
    # Employees, their places and versions (the workforce module).
    workforce_address: str = "127.0.0.1:7001"
    # Tasks (the task module of core-rust): standups read real task status.
    task_address: str = "127.0.0.1:7002"
