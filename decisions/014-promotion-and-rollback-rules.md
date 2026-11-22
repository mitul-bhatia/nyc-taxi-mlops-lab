# 014: Promotion only moves a pipeline-evaluated version; app rollback needs no approval

Stage: S3 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

1. `promote.yaml` runs in the `production` environment (me as required reviewer, manual dispatch). It promotes the `challenger`, or a version I name, only if that version carries every pipeline version tag (contracts/mlflow.yaml) and its own tags still pass decision 010's gate (MAE at least 5% better than its baseline, p95 at most 10 ms). It records the outgoing champion as the tag `previous_champion` on the new version and in the run summary, reloads serving, and checks `/predict` answers with alias champion and that version.
2. `rollback.yaml` (flip to the other colour, no deploy) needs no approval and refuses if the other colour isn't running. Time it from its run summary.
3. Model rollback is not possible yet: with one gated version there is no previous champion. It becomes possible in S8 using `previous_champion`.

## Rejected alternative

Put app rollback behind the same required-reviewer environment as promotion.

## Why

Promotion is a deliberate, low-urgency choice, so a reviewed click costs nothing and prevents an accidental or unevaluated model going live. Rollback is the opposite: it happens during an incident, when waiting for an approval is the cost, and flipping to a colour that was already serving riders is low risk. The trade-off: anyone who can dispatch workflows can roll back; for a solo repo that is only me.

## What would show this was wrong

A rollback triggered by mistake or by someone who shouldn't, or a promotion that went live with a version whose tags were present but not honest (the tag check trusts the pipeline wrote them).
