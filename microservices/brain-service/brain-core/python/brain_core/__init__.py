"""Recall ranking and the write-path governance filter, implemented in Rust (src/).

    fuse(rankings, k=60)                       RRF over retriever rankings
    score(rrf, importance, confidence, trust, kind, age_days)
    rank_and_pack(rankings, candidates, budget, k=60) -> ([(id, score)], tokens_used)
    govern(text, pii_policy="redact") -> (text or None if dropped, findings, instructions)
    trust_for(source) -> "human_verified" | "system" | "observed" | "external"

A candidate is (id, kind, importance, confidence, trust, age_days, tokens).
"""

from brain_core._native import RRF_K, fuse, govern, rank_and_pack, score, trust_for

__all__ = ["RRF_K", "fuse", "govern", "rank_and_pack", "score", "trust_for"]
