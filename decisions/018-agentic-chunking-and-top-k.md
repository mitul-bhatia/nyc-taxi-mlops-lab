# 018: The chat model chooses the chunk boundaries, validated, and every answer uses the top 3 chunks

Stage: S6 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

- **Chunking is agentic.** The chat model (Qwen3-4B on the LLM host) sees a page as numbered paragraphs and answers only with pairs of paragraph numbers (`{"chunks": [[1, 3], [4, 4]]}`). Our code cuts the text, computes each chunk's heading from the page's own headings, and checks the answer (the pairs must tile every paragraph exactly once, at most 8 chunks, none over 400 tokens). Temperature 0 and a fixed seed. If the answer fails twice, or the model errors, that page is chunked by the fixed-size chunker (150 tokens, 20 overlap) and the run's metadata says so. Only pages whose ETag changed are chunked again.
- **Top-k is 3.** Each answer gets the three chunks nearest the question by cosine similarity.
- Every chunk starts with the page title and its heading path (the repeated page title is dropped), so a chunk like "The fee is $4.25" is findable.

## Rejected alternative

A fixed size. I measured the real wiki first: 16 pages, about 1,546 tokens in all, median page about 94 tokens, largest about 181. Fixed chunking gives 16 chunks at 250/40, 17 at 150/20, 22 at 100/15, 29 at 80/15 and 46 at 50/10 (one page has more than one heading).

## Why

I wanted boundaries that follow meaning (keep a rule with its exception) instead of a word count, and the model can do that where a counter can't. Because the model never writes text, it can't rewrite or invent wiki content, and an answer that doesn't tile the page is refused, so a bad answer costs nothing but a fallback. Measured on the first real run: 15 of 16 pages were chunked by the model, 1 (`eta-complaints`, answered `[(1, 4)]` for a 2-paragraph page) failed the check and used the fixed chunker, giving 17 chunks; chunking took 180 s of LLM time; the second run chunked 0 pages and embedded 0. Top-k 3: every chunk is about 100 tokens and costs 8 to 11 s of prompt time on the CPU host, so 3 adds about 30 s to a 45 to 70 s answer; 2 risks missing the exception page, 5 would push answers toward the 150 s limit and gives the small model more to be distracted by.

The honest finding: these pages are so short that the model mostly kept each page whole (17 chunks for 16 pages, the same shape 150/20 gives), so on this wiki agentic chunking costs 180 s of LLM time to get almost what a one-line fixed rule gets for free. I kept it because it should matter when pages get longer, and because the validation and fallback make it safe.

## Evidence from the eval

`app/evals/assist_eval.py` against the live app (10 wiki questions, each with its page and one required fact, plus one control not in the wiki): the right page was in the top 3 for **9 of 10**, the fact was stated for **9 of 10**, the control was declined, and answers took 22 to 44 s (about 570 prompt tokens). The 9 hits ranked 1st (7 times), 2nd and 3rd. The one miss: "What happens to ETA quotes between 1:00 and 4:30 at night?" never retrieved `quiet-hours`, because it uses none of that page's words ("Quiet Hours", "dispatch", "matching radius"), so it embeds nearer other ETA pages; the assistant then correctly said the excerpts don't contain it, rather than guessing. Raising k would be the first thing to try (k=5 costs about 20 s more per answer), but one vague question is not enough evidence to pay that on every request, so k stays 3.

## What would show this was wrong

Chunking time growing with the wiki without chunks getting better, many pages landing on the fallback (watch `pages_by_fallback`), the eval's retrieval or fact checks dropping when k is 3 but passing at 5, or a changed page re-chunked differently each time so its vectors churn.
