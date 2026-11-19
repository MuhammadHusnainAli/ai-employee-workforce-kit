"""Consolidation's pure parts: clusters, cited summaries, lessons, decay."""

from core_python.brain.domain.learn.consolidate import (
    FLOOR,
    Episode,
    clusters,
    decayed,
    distil,
    reflect,
    summarise,
)


def ep(i: int, vec: list[float], outcome: str = "success", task: str = "") -> Episode:
    return Episode(
        memory_id=f"m{i}",
        task=task or f"Summarise ticket batch {i}",
        outcome=outcome,  # type: ignore[arg-type]
        embedding=vec,
    )


A, A2, B = [1.0, 0.0, 0.0], [0.95, 0.1, 0.0], [0.0, 1.0, 0.0]


def test_similar_episodes_cluster_and_singletons_do_not() -> None:
    groups = clusters([ep(1, A), ep(2, B), ep(3, A2), ep(4, A)])
    assert [[e.memory_id for e in g] for g in groups] == [["m1", "m3", "m4"]]


def test_a_summary_cites_its_episodes() -> None:
    g = [ep(1, A, task="Draft the weekly report"), ep(2, A2, "failure"),
         ep(3, A, "escalated", task="Draft the weekly report for Acme")]  # fmt: skip
    s = summarise(g)
    assert s.kind == "semantic" and s.origin == "consolidation"
    assert s.content == (
        'I have done 3 tasks like "Draft the weekly report": 1 succeeded, 1 failed, '
        "1 needed a person."
    )
    assert s.cites == ["m1", "m2", "m3"]


def test_lessons_only_from_repeated_failure() -> None:
    failing = [ep(1, A, "failure"), ep(2, A, "failure"), ep(3, A, "success")]
    lesson = reflect(failing)
    assert lesson is not None and lesson.kind == "reflection" and lesson.confidence == 0.5
    assert reflect(failing[:2]) is None, "too few tries"
    assert reflect([ep(1, A), ep(2, A), ep(3, A, "failure")]) is None, "mostly fine"
    kinds = [c.kind for c in distil([*failing, ep(9, B)])]
    assert kinds == ["semantic", "reflection"]


def test_decay_and_the_floor() -> None:
    assert decayed(0.5) == (0.475, False)
    value, archived = decayed(FLOOR)
    assert value < FLOOR and archived
