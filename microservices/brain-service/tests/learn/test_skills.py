"""Skill mining, pure: what becomes a skill and what does not."""

from brain_service.domain.learn.skills import MIN_EPISODES, Success, describe, mine

A = [1.0, 0.0, 0.0]
A2 = [0.98, 0.05, 0.0]  # the same kind of task, worded differently
B = [0.0, 1.0, 0.0]  # another kind of task
TOOLS = ("word_count", "char_count")


def ok(i: int, emb: list[float] = A, tools: tuple[str, ...] = TOOLS, task: str = "") -> Success:
    return Success(f"m{i}", task or f"Count the words in report {i}", tools, emb)


def test_five_similar_successes_with_one_tool_sequence_make_a_skill() -> None:
    eps = [ok(i, A if i % 2 else A2) for i in range(MIN_EPISODES)]
    (skill,) = mine(eps, set())
    assert skill.tools == TOOLS
    assert skill.evidence == tuple(f"m{i}" for i in range(MIN_EPISODES))
    assert skill.name == "word_count then char_count"
    assert "1. Use word_count.\n2. Use char_count." in skill.procedure
    assert skill.trigger in skill.procedure


def test_too_few_successes_make_nothing() -> None:
    assert mine([ok(i) for i in range(MIN_EPISODES - 1)], set()) == []


def test_different_tool_sequences_do_not_pool() -> None:
    eps = [ok(i) for i in range(3)] + [ok(i, tools=("word_count",)) for i in range(3, 6)]
    assert mine(eps, set()) == []


def test_unlike_tasks_with_the_same_tools_do_not_pool() -> None:
    eps = [ok(i, A) for i in range(3)] + [ok(i, B) for i in range(3, 6)]
    assert mine(eps, set()) == []


def test_runs_without_tools_are_never_a_skill() -> None:
    assert mine([ok(i, tools=()) for i in range(10)], set()) == []


def test_a_known_tool_sequence_is_not_proposed_again() -> None:
    assert mine([ok(i) for i in range(MIN_EPISODES)], {TOOLS}) == []


def test_the_shortest_task_is_the_trigger_and_mining_is_deterministic() -> None:
    eps = [ok(i, task=f"Count words in the quarterly report number {i}") for i in range(5)]
    eps.append(ok(9, task="Count words in a memo"))
    assert mine(eps, set()) == mine(list(eps), set())
    assert mine(eps, set())[0].trigger == "Count words in a memo"


def test_describe_names_each_tool_once() -> None:
    name, procedure = describe("x", ("search", "read", "search"))
    assert name == "search then read"
    assert procedure.endswith("4. Answer from what the tools returned.")
