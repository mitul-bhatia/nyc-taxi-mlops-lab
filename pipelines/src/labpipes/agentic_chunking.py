"""Agentic chunking (S6, decision 018): the chat model decides where a wiki page splits into chunks.

The model only ever sees the page as numbered paragraphs and answers with pairs of paragraph numbers
(`{"chunks": [[1, 3], [4, 4]]}`). It never writes text, so it cannot rewrite, shorten or invent wiki
content: every chunk's text is cut from the page by this code, and its heading is computed here from the
page's own headings. The answer is checked before use (the pairs must cover every paragraph exactly once,
in order, with no chunk over MAX_TOKENS); a page whose answer fails the check, or whose model call errors
twice, is chunked by the fixed-size chunker instead (`chunk_markdown`, FALLBACK_TOKENS/FALLBACK_OVERLAP),
and the method that produced each page's chunks is recorded so the run's metadata says how often that
happened.

Pure Python: the model is passed in as a function (`chat(system, user) -> str`), so tests need no LLM.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .chunking import Chunk, chunk_markdown, estimate_tokens

MAX_TOKENS = 400  # an agentic chunk over this is refused (the embedding model reads 2,048; prompt time is the cost)
FALLBACK_TOKENS, FALLBACK_OVERLAP = 150, 20  # the fixed-size chunker's settings when the model's answer fails
MAX_CHUNKS_PER_PAGE = 8

# Fixed text: llama.cpp caches this prefix, so only the page after it costs prompt time on each call.
SYSTEM_PROMPT = """You prepare wiki pages for search. You are given one page as numbered paragraphs: [1], [2], and so on.
Group CONSECUTIVE paragraphs into chunks, so that each chunk can answer a question on its own.
Rules:
1. Keep a rule together with its numbers, conditions and exceptions.
2. Never split a list or a table.
3. A short page is usually ONE chunk. Split only where the topic clearly changes.
4. A chunk is between 30 and 250 words.
5. Every paragraph is in exactly one chunk, in order.
Reply with JSON only: {"chunks": [[first, last], ...]} where each pair is the first and last paragraph number of one chunk."""


@dataclass(frozen=True)
class Paragraph:
    number: int  # 1-based, as the model sees it
    heading: str  # the heading path this paragraph sits under ("" before the first heading)
    text: str
    is_heading: bool


@dataclass(frozen=True)
class Chunked:
    chunks: list[Chunk]
    method: str  # "llm" or "fallback"
    reason: str  # why the fallback was used, "" for llm


def paragraphs(markdown: str) -> list[Paragraph]:
    """The page's blank-line-separated blocks, each tagged with the heading path it sits under."""
    out: list[Paragraph] = []
    path: list[tuple[int, str]] = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", block) if "\n" not in block else None
        if m:
            level = len(m.group(1))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, m.group(2)))
        out.append(Paragraph(len(out) + 1, " > ".join(t for _, t in path), block, bool(m)))
    return out


def render(paras: list[Paragraph]) -> str:
    return "\n\n".join(f"[{p.number}] {p.text}" for p in paras)


def parse_ranges(answer: str, n: int) -> list[tuple[int, int]]:
    """The model's pairs, checked: they must tile paragraphs 1..n exactly, in order. Raises ValueError if not."""
    try:
        pairs = json.loads(answer)["chunks"]
        ranges = [(int(a), int(b)) for a, b in pairs]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"not the requested JSON: {answer[:80]!r}") from e
    if not ranges or len(ranges) > MAX_CHUNKS_PER_PAGE:
        raise ValueError(f"{len(ranges)} chunks (allowed 1 to {MAX_CHUNKS_PER_PAGE})")
    expected = 1
    for a, b in ranges:
        if a != expected or b < a or b > n:
            raise ValueError(f"ranges do not tile 1..{n}: got {ranges}")
        expected = b + 1
    if expected != n + 1:
        raise ValueError(f"ranges stop at {expected - 1}, page has {n} paragraphs")
    return ranges


def without_title(title: str, heading: str) -> str:
    """Pages open with "# <the page's title>": the prefix line already says the title, so don't repeat it."""
    if heading == title:
        return ""
    return heading[len(title) + 3 :] if heading.startswith(f"{title} > ") else heading


def build(title: str, paras: list[Paragraph], ranges: list[tuple[int, int]]) -> list[Chunk]:
    chunks = []
    for index, (a, b) in enumerate(ranges):
        group = paras[a - 1 : b]
        heading = without_title(title, group[0].heading)
        body = [p.text for p in group if not p.is_heading]  # the heading is in the prefix line
        if not body:  # a range of headings only: nothing to search for
            raise ValueError(f"chunk {a}-{b} has no text under its heading")
        prefix = f"{title} > {heading}" if heading else title
        text = f"{prefix}\n\n" + "\n\n".join(body)
        chunk = Chunk(index, heading, text, estimate_tokens(text))
        if chunk.tokens > MAX_TOKENS:
            raise ValueError(f"chunk {a}-{b} is ~{chunk.tokens} tokens (over {MAX_TOKENS})")
        chunks.append(chunk)
    return chunks


def chunk_page(title: str, markdown: str, chat: Callable[[str, str], str], attempts: int = 2) -> Chunked:
    paras = paragraphs(markdown)
    if not paras:
        return Chunked([], "llm", "")
    reason = ""
    for _ in range(attempts):
        try:
            ranges = parse_ranges(chat(SYSTEM_PROMPT, f"Page title: {title}\n\n{render(paras)}"), len(paras))
            return Chunked(build(title, paras, ranges), "llm", "")
        except Exception as e:  # noqa: BLE001 - any failure means "use the fixed chunker", and says why
            reason = f"{type(e).__name__}: {e}"[:200]
    return Chunked(chunk_markdown(title, markdown, FALLBACK_TOKENS, FALLBACK_OVERLAP), "fallback", reason)
