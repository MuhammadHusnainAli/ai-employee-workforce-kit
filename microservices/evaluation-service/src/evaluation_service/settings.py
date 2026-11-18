from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from wfk_platform import ServiceSettings


class Settings(ServiceSettings):
    """This service's settings. Add service-specific fields here (env prefix WFK_)."""

    model_config = SettingsConfigDict(env_prefix="WFK_", extra="ignore")

    service_name: str = "evaluation-service"
    # A Temporal worker with no port of its own: evaluations start
    # from events, and their results travel as events.
    port: int = 0
    database_url: str = "postgresql://wfk:wfk@127.0.0.1:7017/eval_db"

    # agent-runtime's evaluation queue (`agent-runtime worker --prefix eval- --no-loops`), so
    # evaluations never compete with live work.
    eval_run_queue: str = "eval-runs"
    # Where candidate runs recall from: brain-service, for regression sets.
    brain_address: str = "127.0.0.1:7003"
    # The suites (golden/<employee>.yaml or golden/default.yaml, safety/*.yaml).
    suites_dir: str = "suites"
    # How long one evaluation run may take, and how many run at once.
    run_timeout_s: float = 300
    concurrency: int = 4
