# 013: Blue-green switches automatically after the idle colour passes its smoke test

Stage: S3 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

After CI succeeds on main, CD deploys the commit to the idle colour, then runs `deploy/web.sh smoke <colour> <full SHA>`: `/version` must report exactly that commit and `/predict` must answer, both through Traefik pinned to the idle colour so no rider is involved. Only if both pass does CD run `deploy/web.sh switch`, then it confirms the live URL reports the new SHA. Nobody clicks. The old colour keeps running.

## Rejected alternative

A manual approval (a GitHub environment with a required reviewer) before every switch.

## Why

The riskiest part of a deploy is "does this image start and answer", and the smoke test checks that before any rider is sent to it. A failed smoke leaves traffic on the old colour, so a bad deploy costs nothing. Adding a human click on every push adds delay without adding a check a human can do better than the smoke test. The human gate sits where judgment matters: model promotion (promote.yaml, decision 012), not code that already passed `make check`, tests and the smoke. Trade-off: a bug the smoke test can't see (wrong answers, not a crash) reaches riders; the answer is the one-step rollback (014).

## What would show this was wrong

A deploy that passed smoke but broke riders (the smoke test is too shallow, so add checks), or a deploy that must not go out without review (e.g. a contract change), which would call for the approval gate.

## Evidence from the first run

The first deploy of this stack (live was `none`, so it went to blue) passed smoke on the exact commit and switched in a 41 s job. Every later deploy goes to the idle colour.
