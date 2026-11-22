# 016: Three SLOs: 99.5% available, 95% under 250 ms, 99% answered by the model

Stage: S5 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

Over 28 days, measured on `POST /predict` from the app's own metrics:
- **Availability:** 99.5% of answers are not 5xx (error budget 0.5%).
- **Latency:** 95% of answers take under 250 ms (budget 5%).
- **Model:** 99% of quotes come from the model, not the static-median fallback (budget 1%).

Each pages on a fast burn (14.4x the budget over 30 minutes and 5 minutes, both) and opens a ticket on a slow burn (6x over 6 hours and 30 minutes). Details and reasons per SLO are in `observability/slo.yaml`, the rules in `observability/rules/slo.rules.yaml`, and their promtool tests next to them.

## Rejected alternative

Two stricter SLOs: 99.9% availability and p99 under 100 ms, with no model SLO.

## Why

99.9% leaves about 40 minutes of failures a month. A lab with deploys, restarts and one person on call can't defend that, and a number I can't meet becomes an alert I learn to ignore. Serving's p95 is about 3 ms and the app cuts serving off at 300 ms, so 250 ms is the real boundary riders feel; a 100 ms target would page on a cold start. The third SLO exists because availability is blind to the failure that matters most here: when serving dies the app still answers HTTP 200, with the median, so every dashboard stays green while riders get 12.25 minutes for every trip. Only the model SLI sees that. Paging on a fast burn over two windows (not on any single error) means a one-minute blip doesn't wake me, and the short window makes the alert reset quickly once fixed. The promtool tests check both: an outage pages, a one-minute spike does not.

## What would show this was wrong

An alert that fires with no rider impact (too strict, loosen it), a rider-visible problem that no alert caught (a missing SLI), the budget used up in the first week of a window, or traffic so low that one bad request moves a ratio (then use longer windows or a minimum request count).
