import time
import uuid

import pytest
from pydantic import ValidationError

from wfk_platform.ids import uuid7
from wfk_platform.outbox import CloudEvent, new_event

TENANT = uuid.UUID("0193a7c4-0000-7000-8000-000000000001")


def test_uuid7_has_version_7_and_rfc_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_sorts_by_creation_time() -> None:
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a < b


def test_event_round_trips_through_json() -> None:
    e = new_event("run.completed", "//cell-local/agent-runtime", TENANT, {"run_id": "r1"})
    back = CloudEvent.from_json(e.to_json())
    assert back == e
    assert back.specversion == "1.0"


def test_event_type_must_be_a_subject() -> None:
    with pytest.raises(ValidationError):
        new_event("RunCompleted", "//x", TENANT, {})
    with pytest.raises(ValidationError):
        new_event("run", "//x", TENANT, {})


def test_parses_the_shared_contract_example() -> None:
    """Rust services parse the same file (tests/platform_outbox_inbox.rs)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]  # the service folder
    raw = (root / "contracts/events/examples/run.completed.json").read_bytes()
    event = CloudEvent.from_json(raw)
    assert event.type == "run.completed"
    assert event.time.microsecond == 161893
    assert CloudEvent.from_json(event.to_json()) == event


def test_template_migration_matches_library_schema() -> None:
    """Each module's first migration is a copy of the platform schema; keep them in step."""
    from importlib import resources
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]  # the service folder
    library = resources.files("wfk_platform").joinpath("sql/outbox_inbox.sql").read_bytes()
    templates = sorted(root.glob("migrations/*/0001_platform_outbox_inbox.up.sql"))
    modules = [t.parent.name for t in templates]
    assert modules == ["agent_runtime", "brain", "evaluation", "workforce"]
    for template in templates:
        assert template.read_bytes() == library, template
