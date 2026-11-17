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
    """Rust parses the same file (libs/rust/wfk-platform/tests/outbox_inbox.rs)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    raw = (root / "contracts/events/examples/run.completed.json").read_bytes()
    event = CloudEvent.from_json(raw)
    assert event.type == "run.completed"
    assert event.time.microsecond == 161893
    assert CloudEvent.from_json(event.to_json()) == event
