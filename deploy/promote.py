"""Promotion (S3): point the `champion` alias at a version the pipeline evaluated. Run by promote.yaml
inside the serving image (it has mlflow). Only this workflow moves champion (contracts/mlflow.yaml).

usage: promote.py [VERSION]     default: the version the `challenger` alias points at
env:   MARGIN, LATENCY_BUDGET_MS   decision 010's gate, re-checked here from the version's own tags
"""
import os
import sys

from mlflow import MlflowClient

NAME = "trip-duration"
MARGIN = float(os.environ["MARGIN"])
LATENCY = float(os.environ["LATENCY_BUDGET_MS"])
REQUIRED = ["training_window", "holdout_month", "features_version", "git_sha", "mae_min",
            "p95_latency_ms", "champion_mae_min"]  # contracts/mlflow.yaml version_tags

client = MlflowClient()
arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
if arg:
    version = arg
else:
    try:
        version = client.get_model_version_by_alias(NAME, "challenger").version
    except Exception:
        sys.exit("no version named and no challenger alias: nothing to promote")

mv = client.get_model_version(NAME, version)
tags = mv.tags
missing = [t for t in REQUIRED if t not in tags]
if missing:
    sys.exit(f"v{version} was not evaluated by the pipeline (missing tags: {missing})")
mae, base, p95 = float(tags["mae_min"]), float(tags["champion_mae_min"]), float(tags["p95_latency_ms"])
if mae > base * (1 - MARGIN):
    sys.exit(f"v{version}: MAE {mae:.3f} does not beat {base:.3f} by {MARGIN:.0%}")
if p95 > LATENCY:
    sys.exit(f"v{version}: p95 {p95:.1f} ms is over the {LATENCY:.1f} ms budget")

try:
    outgoing = client.get_model_version_by_alias(NAME, "champion").version
except Exception:
    outgoing = "none"
if outgoing == version:
    print(f"v{version} is already champion")
else:
    client.set_model_version_tag(NAME, version, "previous_champion", outgoing)
    client.set_registered_model_alias(NAME, "champion", version)
print(f"promoted v{version} (MAE {mae:.3f}, p95 {p95:.1f} ms); outgoing champion: v{outgoing}")
print(f"OUT version={version}")
print(f"OUT outgoing={outgoing}")
