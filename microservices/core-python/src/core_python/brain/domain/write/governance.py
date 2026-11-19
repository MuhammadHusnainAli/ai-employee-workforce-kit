"""The governance filter (brain.md sections 4 and 7), around brain_core.govern (Rust).

- Secrets are always removed; PII follows the tenant's policy (keep, redact, drop).
- Every memory gets a trust tag from where it came from.
- Text that tries to instruct the employee is stored as data, never as a rule. Unless a person
  wrote it, it goes to quarantine: invisible to recall until a human approves it in the
  Brain Inspector. That is the defence against memory poisoning ("always CC x@evil.com").
"""

from __future__ import annotations

import brain_core

from core_python.brain.domain.graph.model import EntityRef, RelationRef, normalize
from core_python.brain.domain.write.model import Candidate, Governed, PiiPolicy


def govern(candidate: Candidate, pii_policy: PiiPolicy) -> Governed | None:
    """The governed candidate, or None when it must not be stored at all."""
    text, findings, instructions = brain_core.govern(candidate.content, pii_policy)
    if text is None:
        return None
    trust = brain_core.trust_for(candidate.origin)
    flags = dict(findings)
    if instructions:
        flags["instructions"] = 1
    quarantine = instructions and trust != "human_verified"
    mentions, relations = ([], []) if quarantine else _graph(candidate, pii_policy)
    return Governed(
        candidate=candidate,
        content=text,
        trust=trust,  # type: ignore[arg-type]  # brain_core returns one of the four tags
        status="quarantined" if quarantine else "active",
        findings=flags,
        mentions=mentions,
        relations=relations,
    )


def _clean_name(name: str, pii_policy: PiiPolicy) -> bool:
    """A name is kept only if the filter leaves it untouched: no secret, and no personal data
    the tenant's policy would redact or drop."""
    text, findings, _ = brain_core.govern(name, pii_policy)
    return text == name and not findings.get("secret")


def _graph(c: Candidate, pii_policy: PiiPolicy) -> tuple[list[EntityRef], list[RelationRef]]:
    """The candidate's mentions and relations that may enter the graph. A relation needs
    both of its ends among the kept mentions (or kept by name)."""
    mentions = [
        m.model_copy(update={"aliases": [a for a in m.aliases if _clean_name(a, pii_policy)]})
        for m in c.mentions
        if _clean_name(m.name, pii_policy)
    ]
    kept = {m.norm for m in mentions}
    relations: list[RelationRef] = []
    for r in c.relations:
        ends = (r.subject, r.object)
        if not all(_clean_name(n, pii_policy) for n in ends):
            continue
        for n in ends:  # an end the extractor did not list as a mention becomes one
            if normalize(n) not in kept:
                mentions.append(EntityRef(name=n))
                kept.add(normalize(n))
        relations.append(r)
    return mentions, relations
