"""The semantic graph (brain.md section 2): entities and the relations between them. Pure.

An entity is a person, organisation, project, system or place the employee knows about. Names
are compared in a normal form, so "The Acme Corp." and "acme corp" are the same entity; other
names for it are aliases. A relation is a typed edge ("ana works_at acme") that holds while the
fact stating it holds.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EntityType = Literal["person", "organization", "project", "system", "place", "other"]
ENTITY_TYPES: tuple[EntityType, ...] = (
    "person", "organization", "project", "system", "place", "other",
)  # fmt: skip

_PUNCT = re.compile(r"[^\w\s@.-]+")
_SPACE = re.compile(r"\s+")
_PREDICATE = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """The comparison form of a name: lower case, no punctuation, single spaces, no leading
    "the", no trailing dots."""
    n = _SPACE.sub(" ", _PUNCT.sub(" ", name.lower())).strip().rstrip(".").strip()
    return n.removeprefix("the ").strip()


def predicate(text: str) -> str:
    """A relation type in snake case: "Works At" -> "works_at"."""
    p = _PREDICATE.sub("_", text.lower()).strip("_")
    if not p or not p[0].isalpha():
        p = "rel_" + p
    return p[:61]


class EntityRef(BaseModel):
    """An entity as the extractor sees it: a name, a type and other names for it."""

    name: str = Field(min_length=1, max_length=200)
    type: EntityType = "other"
    aliases: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("type", mode="before")
    @classmethod
    def _known_type(cls, v: object) -> object:
        return v if v in ENTITY_TYPES else "other"

    @property
    def norm(self) -> str:
        return normalize(self.name)

    def norm_aliases(self) -> list[str]:
        return sorted({a for a in (normalize(x) for x in self.aliases) if a and a != self.norm})


class RelationRef(BaseModel):
    """A relation as the extractor sees it, by entity names."""

    subject: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=80)
    object: str = Field(min_length=1, max_length=200)

    @property
    def predicate(self) -> str:
        return predicate(self.type)
