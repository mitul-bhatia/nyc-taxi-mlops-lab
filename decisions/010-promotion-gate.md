# 010: A new version must beat what is serving by 5% on MAE, within 10 ms p95

Stage: S2 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The blocking `beats_champion` check (and the `challenger` asset, which re-checks the same rule) promotes a version only if its overall holdout MAE (`mae_min`, minutes, as `contracts/mlflow.yaml` defines it) is at least 5% below the champion's, or the v1 baseline's when there is no champion yet, and its p95 single-row prediction latency is at most 10 ms. The pipeline only ever sets `challenger`; `champion` is moved only by the approval-gated promotion workflow.

## Rejected alternative

A 10% margin, which would have rejected the first real model, or a 2% margin, which lets in differences that one month of data cannot tell from luck. A 5 ms or 25 ms latency budget instead of 10 ms.

## Why

Measured on the held-out month (2024-04, 3.48M trips) after training on 2024-01 to 2024-03: the LightGBM model has MAE 3.64 min against the baseline's 3.96 min (8.1% better) and p95 latency 3.0 ms against 0.8 ms. A 5% margin lets that real gain through with room to spare, and is large enough that it is unlikely to be month-to-month noise. A 10 ms budget is about three times the model's latency: it stops a much heavier model, but a timing run on a busy host will not fail a good one. MAE is a metric the contract fixes and it is robust to the few three-hour trips that would dominate an RMSE. The cost: a version that is 3 to 4% better is rejected even if the gain is real, and a more accurate but slower model is rejected until it is made faster.

## What would show this was wrong

A promoted model that is no better (or worse) on live trips than the one it replaced, which would mean the margin is too small; a real improvement repeatedly rejected by the margin or the budget; or serving latency that is noticeably higher than the in-process p95 measured here. Also revisit the slices (airport 5.98, outer 7.40 minutes) if one gets worse while overall MAE improves, since the gate looks only at the overall figure.
