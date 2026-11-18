"""The governance filter (brain.md sections 4 and 7), around brain_core.govern (Rust).

- Secrets are always removed; PII follows the tenant's policy (keep, redact, drop).
- Every memory gets a trust tag from where it came from.
- Text that tries to instruct the employee is stored as data, never as a rule. Unless a person
  wrote it, it goes to quarantine: invisible to recall until a human approves it in the
  Brain Inspector. That is the defence against memory poisoning ("always CC x@evil.com").
"""

from __future__ import annotations

import brain_core

from brain_service.domain.write.model import Candidate, Governed, PiiPolicy


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
    return Governed(
        candidate=candidate,
        content=text,
        trust=trust,  # type: ignore[arg-type]  # brain_core returns one of the four tags
        status="quarantined" if quarantine else "active",
        findings=flags,
    )
