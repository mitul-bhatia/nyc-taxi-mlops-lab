"""Agentic chunking (decision 018) without an LLM: the model is a function we script."""

import json

import pytest

from labpipes.agentic_chunking import (MAX_TOKENS, build, chunk_page, paragraphs, parse_ranges)

PAGE = """# Airport fares

Public rules for airport trips.

## Fee

The curb fee is $4.50 for trips ending at the airport.

1. It shows on receipts as AIR-1.
2. It is capped at one per trip.

## Exceptions

Trips under 2 miles do not pay the fee."""


def scripted(answer):
    calls = []

    def chat(system, user):
        calls.append((system, user))
        return answer if isinstance(answer, str) else answer.pop(0)

    chat.calls = calls
    return chat


def test_paragraphs_carry_their_heading_path():
    ps = paragraphs(PAGE)
    assert [p.number for p in ps] == list(range(1, len(ps) + 1))
    assert [(p.heading, p.is_heading) for p in ps][:4] == [("Airport fares", True), ("Airport fares", False),
                                                           ("Airport fares > Fee", True), ("Airport fares > Fee", False)]
    assert ps[-1].heading == "Airport fares > Exceptions" and "2 miles" in ps[-1].text


def test_a_good_answer_cuts_the_page_exactly_where_the_model_said():
    n = len(paragraphs(PAGE))
    chat = scripted(json.dumps({"chunks": [[1, 2], [3, 6], [7, n]]}))
    result = chunk_page("Airport fares", PAGE, chat)
    assert result.method == "llm" and result.reason == ""
    assert [c.heading for c in result.chunks] == ["", "Fee", "Exceptions"]
    assert "$4.50" in result.chunks[1].text and "AIR-1" in result.chunks[1].text and "capped" in result.chunks[1].text
    assert result.chunks[2].text.startswith("Airport fares > Exceptions\n\nTrips under 2 miles")
    assert len(chat.calls) == 1


def test_chunk_text_only_ever_comes_from_the_page():
    n = len(paragraphs(PAGE))
    result = chunk_page("Airport fares", PAGE, scripted(json.dumps({"chunks": [[1, n]]})))
    page_words = set(PAGE.replace("#", " ").split()) | {"Airport", "fares", ">"}
    assert set(result.chunks[0].text.split()) <= page_words


@pytest.mark.parametrize("bad", [
    "not json at all",
    '{"chunks": []}',
    '{"chunks": [[2, 9]]}',            # does not start at 1
    '{"chunks": [[1, 3], [5, 9]]}',    # a gap
    '{"chunks": [[1, 5], [4, 9]]}',    # an overlap
    '{"chunks": [[1, 2]]}',            # stops early
    '{"chunks": [[1, 99]]}',           # past the end
    '{"other": 1}',
])
def test_a_bad_answer_is_refused_twice_then_the_fixed_chunker_is_used(bad):
    chat = scripted(bad)
    result = chunk_page("Airport fares", PAGE, chat)
    assert result.method == "fallback" and result.reason
    assert len(chat.calls) == 2  # one retry
    assert any("$4.50" in c.text for c in result.chunks)  # still complete: nothing is lost


def test_a_model_error_falls_back_too():
    def broken(system, user):
        raise TimeoutError("llm host down")

    result = chunk_page("Airport fares", PAGE, broken)
    assert result.method == "fallback" and "TimeoutError" in result.reason and result.chunks


def test_the_second_attempt_can_succeed():
    n = len(paragraphs(PAGE))
    result = chunk_page("Airport fares", PAGE, scripted(["oops", json.dumps({"chunks": [[1, n]]})]))
    assert result.method == "llm" and len(result.chunks) == 1


def test_a_chunk_of_headings_only_or_over_the_size_cap_is_refused():
    ps = paragraphs(PAGE)
    with pytest.raises(ValueError, match="no text"):
        build("t", ps, [(1, 1), (2, len(ps))])
    long_page = "# T\n\n" + " ".join(f"word{i}" for i in range(MAX_TOKENS))
    with pytest.raises(ValueError, match="over"):
        build("t", paragraphs(long_page), [(1, 2)])


def test_same_text_same_hash_so_it_is_embedded_once():
    n = len(paragraphs(PAGE))
    a = chunk_page("Airport fares", PAGE, scripted(json.dumps({"chunks": [[1, n]]}))).chunks[0]
    b = chunk_page("Airport fares", PAGE, scripted(json.dumps({"chunks": [[1, n]]}))).chunks[0]
    assert a.content_hash("airport") == b.content_hash("airport")


def test_parse_ranges_accepts_a_tiling():
    assert parse_ranges('{"chunks": [[1, 3], [4, 4]]}', 4) == [(1, 3), (4, 4)]
