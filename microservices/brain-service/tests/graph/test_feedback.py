"""What feedback becomes, before any I/O."""

import uuid
from typing import Any

from brain_service.domain import feedback as fb

TENANT = uuid.UUID("0193a7c4-0000-7000-8000-000000000001")


def f(**kw: Any) -> fb.FeedbackIn:
    base: dict[str, Any] = {"tenant_id": TENANT, "employee_id": "sam", "kind": "correction",
                            "text": "Don't CC finance on small invoices.", "actor_id": "maria",
                            "actor_kind": "human", "idempotency_key": "k1"}  # fmt: skip
    return fb.FeedbackIn.model_validate(base | kw)


def test_only_a_persons_correction_proposes_a_rule() -> None:
    assert fb.proposes_rule(f())
    assert not fb.proposes_rule(f(actor_kind="employee"))
    assert not fb.proposes_rule(f(actor_kind="service"))
    assert not fb.proposes_rule(f(kind="comment"))


def test_what_each_kind_needs() -> None:
    assert fb.problem(f()) is None
    assert fb.problem(f(text=" ")) == "correction needs text"
    assert fb.problem(f(kind="rating", text="")) == "rating needs rating = 1 or -1"
    assert fb.problem(f(kind="rating", rating=1)) == "rating needs the run it rates"
    assert fb.problem(f(kind="rating", rating=-1, run_id="r1")) is None
    assert fb.problem(f(text="x" * 2001)) is not None


def test_rule_text_keeps_people_but_never_secrets() -> None:
    assert fb.rule_text(" Email Ana at ana@example.com first. ") == (
        "Email Ana at ana@example.com first."
    )
    assert "hunter22" not in (fb.rule_text("Use password=hunter22 for staging.") or "")


def test_risk() -> None:
    assert fb.risk("Reply in French to Ana.") == "medium"
    assert fb.risk("Never pay invoices over 500 EUR without asking.") == "high"
    assert fb.risk("Forward contracts to legal.") == "high"


def test_ids_are_stable_per_key() -> None:
    assert f().id == f().id
    assert f().id != f(idempotency_key="k2").id
