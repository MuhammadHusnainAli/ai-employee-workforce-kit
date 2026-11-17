"""brain-core (Rust) through its Python bindings, checked against brain.md section 3."""

import math
import random

import pytest

import brain_core

TRUST_W = {"human_verified": 1.0, "system": 0.9, "observed": 0.7, "external": 0.4}
TAU = {"episodic": 30.0, "reflection": 90.0, "semantic": math.inf}


def reference_score(
    rrf: float, importance: float, confidence: float, trust: str, kind: str, age: float
) -> float:
    """brain.md, written independently in Python."""
    decay = 1.0 if math.isinf(TAU[kind]) else math.exp(-max(age, 0.0) / TAU[kind])
    return rrf * (0.5 + 0.5 * importance) * confidence * TRUST_W[trust] * decay


def test_rrf_uses_k_60_and_one_based_ranks() -> None:
    fused = brain_core.fuse([["a", "b"], ["b", "c"]])
    assert brain_core.RRF_K == 60
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)


def test_score_matches_the_formula_over_many_random_inputs() -> None:
    rng = random.Random(7)
    for _ in range(500):
        args = (
            rng.random() * 0.1,
            rng.random(),
            rng.random(),
            rng.choice(list(TRUST_W)),
            rng.choice(list(TAU)),
            rng.random() * 400,
        )
        assert brain_core.score(*args) == pytest.approx(reference_score(*args), rel=1e-12)


def test_rank_and_pack_orders_by_score_and_respects_the_budget() -> None:
    rankings = [["fresh", "old", "rumour"], ["old", "fresh"]]
    candidates = [
        ("fresh", "episodic", 0.5, 1.0, "system", 1.0, 40),
        ("old", "episodic", 0.5, 1.0, "system", 120.0, 40),  # four time constants old
        ("rumour", "semantic", 0.5, 1.0, "external", 0.0, 40),
    ]
    chosen, used = brain_core.rank_and_pack(rankings, candidates, budget=80)
    assert [i for i, _ in chosen] == ["fresh", "rumour"]
    assert used == 80
    assert chosen[0][1] > chosen[1][1]


def test_an_item_too_big_is_skipped_but_smaller_ones_still_fit() -> None:
    candidates = [
        ("huge", "semantic", 1.0, 1.0, "human_verified", 0.0, 500),
        ("small", "semantic", 0.0, 1.0, "system", 0.0, 10),
    ]
    chosen, used = brain_core.rank_and_pack([["huge", "small"]], candidates, budget=100)
    assert [i for i, _ in chosen] == ["small"] and used == 10


def test_ranking_is_deterministic() -> None:
    candidates = [(f"m{i}", "semantic", 0.5, 1.0, "system", 0.0, 1) for i in range(50)]
    rankings = [[f"m{i}" for i in range(50)][::-1]]
    first = brain_core.rank_and_pack(rankings, candidates, budget=1000)
    assert all(
        brain_core.rank_and_pack(rankings, candidates, budget=1000) == first for _ in range(5)
    )


@pytest.mark.parametrize(
    "candidate",
    [
        ("x", "dream", 0.5, 1.0, "system", 0.0, 1),
        ("x", "semantic", 0.5, 1.0, "gossip", 0.0, 1),
    ],
)
def test_unknown_kind_or_trust_is_an_error(candidate: tuple) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError, match="unknown"):
        brain_core.rank_and_pack([["x"]], [candidate], budget=10)
