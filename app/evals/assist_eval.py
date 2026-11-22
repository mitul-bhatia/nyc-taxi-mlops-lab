"""Small sample-based check of the wiki assistant (S6): each question has the wiki page that holds the
answer and one fact the answer must contain. Run it against the live app (only an allowed IP can reach it):

    uv run python app/evals/assist_eval.py http://<app_public_ip>

It reports, per question, whether the right page was retrieved (sources) and whether the answer states the
fact, with the time it took. The control question is not in the wiki: the assistant must decline.
Not part of `make check`: it needs the live LLM host and about a minute per question.
"""

import json
import sys
import time
import urllib.request

# (question, page id that holds the answer, a fact the answer must contain)
CASES = [
    ("Why does my ETA from JFK include extra minutes, and how many?", "airport-buffer", "7"),
    ("How much is the Harbor Line surcharge?", "harbor-line", "4.25"),
    ("What happens to ETA quotes between 1:00 and 4:30 at night?", "quiet-hours", "2.4"),
    ("How much is surge capped at during a winter storm warning?", "surge-cap", "1.6"),
    ("How long can a driver be online before a forced break?", "driver-rest", "10"),
    ("What is the return fee for a lost item across boroughs?", "lost-items", "27"),
    ("When do I give a rider an ETA credit and what code do I use?", "eta-complaints", "ETA-CREDIT-5"),
    ("Who do I page for a wrong ETA prediction?", "oncall", "atlas-eta"),
    ("Why are weekday evening quotes higher around Midtown and Penn Station?", "blue-corridor", "12"),
    ("Does the congestion toll change the ETA?", "congestion-pricing", "fare"),
]
CONTROL = "What is the capital of France?"


def ask(base: str, question: str) -> tuple[dict, float]:
    req = urllib.request.Request(f"{base}/assist", json.dumps({"question": question}).encode(), {"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=170) as r:
        return json.load(r), time.perf_counter() - t0


def main(base: str) -> int:
    found = right = 0
    for question, page, fact in CASES:
        d, secs = ask(base, question)
        got = [s["page_id"] for s in d["sources"]]
        in_sources = page in got
        in_answer = fact.lower() in d["answer"].lower()
        found += in_sources
        right += in_answer
        print(f"{'ok ' if in_sources and in_answer else 'BAD'} {secs:5.1f}s  page {'found' if in_sources else 'MISSING'} (rank {got.index(page) + 1 if in_sources else '-'})"
              f"  fact {'stated' if in_answer else 'MISSING'}  | {question}")
        if not in_answer:
            print(f"      answer: {d['answer'][:200]}")
    d, secs = ask(base, CONTROL)
    declined = any(w in d["answer"].lower() for w in ("do not have", "don't have", "not have that", "no information", "cannot", "can't"))
    print(f"{'ok ' if declined else 'BAD'} {secs:5.1f}s  control (not in the wiki) {'declined' if declined else 'ANSWERED: ' + d['answer'][:120]}")
    n = len(CASES)
    print(f"\nretrieval: right page in the top-3 for {found}/{n}; answers state the fact for {right}/{n}; control declined: {declined}")
    return 0 if found == right == n and declined else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/")))
