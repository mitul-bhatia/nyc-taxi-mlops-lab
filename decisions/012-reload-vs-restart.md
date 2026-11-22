# 012: Serving picks up a new champion with POST /reload, not a restart

Stage: S3 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

Promotion moves the `champion` alias in MLflow, then calls `deploy/web.sh reload champion` (POST /reload on serving-champion). The container loads and warms the new model while the old one keeps answering, then swaps. Serving also loads the alias at start-up, so a restarted container comes up on whatever the alias says.

## Rejected alternative

Restart the serving-champion container on every promotion (load at start-up only).

## Why

A restart leaves a gap of several seconds with no model. In that gap the app falls back to the static 12.25-minute median, so riders would silently get worse quotes during a promotion, which is the moment we most want to look good. A reload has no gap: a rider either gets the old model or the new one. The cost: the new model goes live the instant the swap happens, so promote.yaml checks `/predict` right after and the reviewer approval is the only gate before that. If the new model fails to load, the old one keeps serving, which is a safe failure.

## What would show this was wrong

A reload that leaves serving in a half-loaded or inconsistent state (e.g. `/model` and `/predict` disagree on the version), memory growth from two models held at once, or a promotion where riders saw a median fallback.
