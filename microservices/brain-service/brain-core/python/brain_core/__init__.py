"""Recall fusion, ranking and packing, implemented in Rust (src/lib.rs).

    fuse(rankings, k=60)                       RRF over retriever rankings
    score(rrf, importance, confidence, trust, kind, age_days)
    rank_and_pack(rankings, candidates, budget, k=60) -> ([(id, score)], tokens_used)

A candidate is (id, kind, importance, confidence, trust, age_days, tokens).
"""

from brain_core._native import RRF_K, fuse, rank_and_pack, score

__all__ = ["RRF_K", "fuse", "rank_and_pack", "score"]
