"""Entity names and relation types in their comparison forms."""

import pytest

from brain_service.domain.graph.model import EntityRef, RelationRef, normalize, predicate


@pytest.mark.parametrize(
    ("name", "norm"),
    [
        ("Acme Corp", "acme corp"),
        ("The Acme Corp.", "acme corp"),
        ("  ACME   corp  ", "acme corp"),
        ("Ana López", "ana lópez"),
        ("billing-api", "billing-api"),
        ("ana@example.com", "ana@example.com"),
    ],
)
def test_normalize(name: str, norm: str) -> None:
    assert normalize(name) == norm


def test_predicates_are_snake_case() -> None:
    assert predicate("Works At") == "works_at"
    assert predicate("reports-to") == "reports_to"
    assert predicate("2nd owner") == "rel_2nd_owner"
    assert RelationRef(subject="Ana", type="Owns", object="Zephyr").predicate == "owns"


def test_entity_ref() -> None:
    aliases = ["ACME", "acme corp", "Acme Inc"]
    e = EntityRef.model_validate({"name": "The Acme Corp.", "type": "company", "aliases": aliases})
    assert e.type == "other", "unknown types fall back to other"
    assert e.norm == "acme corp"
    assert e.norm_aliases() == ["acme", "acme inc"], "the name itself is not an alias"
