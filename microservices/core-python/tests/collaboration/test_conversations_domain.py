"""Conversation rules. No I/O."""

from core_python.collaboration.domain.conversations import (
    MAX_ANSWER_WORDS,
    MAX_CALL_TURNS,
    PERSON,
    Colleague,
    call_prompt,
    pair_key,
    speaker,
    trim_words,
)


def test_a_pair_has_one_key_whoever_starts() -> None:
    assert pair_key("ana", "bo") == pair_key("bo", "ana") == "ana|bo"


def test_who_speaks_comes_from_the_actor() -> None:
    assert speaker("employee", "sales-head") == ("sales-head", "employee", "sales-head")
    assert speaker("person", "maria") == (PERSON, "person", "maria")


def test_answers_are_cut_to_the_word_limit() -> None:
    long = " ".join(["word"] * (MAX_ANSWER_WORDS + 20))
    cut = trim_words(long, MAX_ANSWER_WORDS)
    assert len(cut.split()) == MAX_ANSWER_WORDS
    assert cut.endswith("…")
    assert trim_words("short answer", MAX_ANSWER_WORDS) == "short answer"


def test_the_callee_answers_in_role_with_the_call_so_far() -> None:
    callee = Colleague(
        employee_id="marketing",
        name="Mia",
        title="Head of Marketing",
        role="Runs marketing",
        instructions="Know the price sheet.",
        model="m",
    )
    system, turns = call_prompt(
        callee,
        "sales-junior",
        [("question", "Is there a price sheet?"), ("answer", "Yes.")],
        "Where is it?",
    )
    assert "Mia" in system and "Head of Marketing" in system and "Know the price sheet." in system
    assert "sales-junior" in system and str(MAX_ANSWER_WORDS) in system
    assert [(role, text) for role, text in turns] == [
        ("user", "Is there a price sheet?"),
        ("assistant", "Yes."),
        ("user", "Where is it?"),
    ]
    assert MAX_CALL_TURNS == 6
