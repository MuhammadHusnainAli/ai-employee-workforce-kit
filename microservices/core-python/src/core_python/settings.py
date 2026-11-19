"""core-python's settings, and the per-module settings built from them.

Each module keeps its own settings class (env prefix WFK_, so WFK_EXTRACTOR, WFK_VALKEY_URL and
the rest still apply). What differs per module in one process is set here: each module's own
database, and the address it calls other modules on, which is this process's own port.
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings

from core_python.agent_runtime.settings import Settings as RuntimeSettings
from core_python.brain.settings import Settings as BrainSettings
from core_python.evaluation.settings import Settings as EvalSettings
from core_python.workforce.settings import Settings as WorkforceSettings

PG = "postgresql://wfk:wfk@127.0.0.1:7017"


class Settings(ServiceSettings):
    """Env prefix WFK_. Each module owns one database (ADR-0017 keeps them apart)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "core-python"
    # One local port, from the 7000-7030 range (deployments/README.md).
    port: int = 7001

    workforce_database_url: str = f"{PG}/workforce_db"
    runtime_database_url: str = f"{PG}/run_db"
    brain_database_url: str = f"{PG}/brain_db"
    eval_database_url: str = f"{PG}/eval_db"
    # Where `core-python migrate` finds migrations/<module>/.
    migrations_dir: str = "migrations"

    # The Temporal workers, inboxes and outbox relays. Off: gRPC only (no Temporal needed).
    workers: bool = True
    # The agent's model through the Model Gateway: scripted, local-mock, claude-haiku.
    worker_model: str = "local-mock"

    @property
    def self_address(self) -> str:
        """How one module reaches another: through this process's own gRPC port."""
        return f"127.0.0.1:{self.port}"


def workforce(s: Settings) -> WorkforceSettings:
    return WorkforceSettings(database_url=s.workforce_database_url, port=s.port)


def runtime(s: Settings) -> RuntimeSettings:
    return RuntimeSettings(
        database_url=s.runtime_database_url,
        port=s.port,
        brain_address=s.self_address,
        workforce_address=s.self_address,
    )


def brain(s: Settings) -> BrainSettings:
    return BrainSettings(database_url=s.brain_database_url, port=s.port)


def evaluation(s: Settings) -> EvalSettings:
    return EvalSettings(database_url=s.eval_database_url, port=s.port, brain_address=s.self_address)
