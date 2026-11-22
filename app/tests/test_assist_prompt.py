"""The assistant's prompt (S6, decision 018): a fixed cacheable prefix, excerpts in the user turn."""

from rideapp.assistant import SYSTEM_PROMPT, TOP_K, Chunk, build_messages


def chunk(i, title, content):
    return Chunk(id=f"c{i}", page_id=f"p{i}", title=title, content=content, score=0.9 - i / 10)


def test_system_prompt_is_fixed_and_excerpts_go_in_the_user_turn():
    a = build_messages("Why $4.50?", [chunk(1, "Airport fares", "The curb fee is $4.50.")])
    b = build_messages("How long?", [chunk(2, "Quote rules", "Rounded up.")])
    assert a[0] == b[0] == {"role": "system", "content": SYSTEM_PROMPT}  # same prefix every time: llama.cpp caches it
    assert "ONLY" in SYSTEM_PROMPT and "never guess" in SYSTEM_PROMPT
    assert "The curb fee is $4.50." in a[1]["content"] and "Airport fares" in a[1]["content"]
    assert a[1]["content"].rstrip().endswith("Question: Why $4.50?")


def test_excerpts_come_before_the_question_in_rank_order():
    msg = build_messages("q?", [chunk(1, "A", "first"), chunk(2, "B", "second")])[1]["content"]
    assert msg.index("first") < msg.index("second") < msg.index("Question: q?")


def test_no_chunks_says_so_instead_of_inviting_a_guess():
    msg = build_messages("q?", [])[1]["content"]
    assert "no wiki excerpts" in msg and msg.endswith("Question: q?")


def test_top_k_is_three():
    assert TOP_K == 3
