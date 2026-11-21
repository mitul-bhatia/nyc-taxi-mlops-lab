"""S2: training_set, trained_model, eval_report, the challenger gate and the challenger asset.

- training_set: UNLOAD a window of staging.trip_features (and the holdout month) to the artifact bucket.
  The zone-pair distance lookup (contracts/features.yaml, est_distance_miles) comes from the window only.
- trained_model: fit tripmodel.LightGBMModel (the given v2), log it to MLflow as a pyfunc with
  tripmodel.model_signature() and the tripmodel package bundled, register a version of the model named
  in contracts/mlflow.yaml, and tag it as that contract says.
- eval_report: tripmodel.evaluate the new version and the champion (or the v1 baseline before there is
  one) on the same holdout; log the metrics.
- A blocking check (beats_champion) that guards a `challenger` asset, which sets the alias. The pipeline
  never moves `champion`.

Decisions: the window (decisions/008-training-window.md): train on the WINDOW_MONTHS months before the
newest month that has features, and test on that newest month, never on a random split of the same
months. What stays in the notebook (decisions/009): the notebook explores; everything repeatable that
produces a registered model is here. The promotion rule is decision 010 (decisions/010-promotion-gate.md):
overall holdout MAE (the contract's mae_min) must beat the champion's, or the v1 baseline's before there is
one, by MARGIN, and p95 single-row latency must stay within LATENCY_BUDGET_MS. If either constant is None
the gate fails closed and no challenger is ever set.
"""

import os
from pathlib import Path

import dagster as dg
import mlflow
import pandas as pd

import tripmodel
from tripmodel import BaselineModel, LightGBMModel, evaluate, model_signature

from ..contracts import features_contract, mlflow_contract
from ..partitions import check_month
from ..resources import ArtifactBucket, MlflowTracking, RedshiftData
from ..runs import last_metadata

WINDOW_MONTHS = 3  # decision 008: the months that train; the month after them is the holdout

# Decision 010 (promotion metric and margin). None would mean "not decided": the gate then fails closed.
MARGIN: float | None = 0.05  # the new MAE must be at least this fraction below the champion's (or baseline's)
LATENCY_BUDGET_MS: float | None = 10.0  # p95 single-row predict latency must not exceed this

STAGING = "staging.trip_features"


def plan_window(months: list[str]) -> tuple[list[str], str]:
    """(training months, holdout month): the holdout is the newest month, the window the months before it."""
    months = sorted(check_month(m) for m in months)
    if len(months) < WINDOW_MONTHS + 1:
        raise dg.Failure(f"need {WINDOW_MONTHS + 1} months of trip_features to train and test, have {len(months)}: {months}")
    return months[-1 - WINDOW_MONTHS : -1], months[-1]


def in_list(months: list[str]) -> str:
    """('2024-01', '2024-02'), months validated first because they're formatted into SQL."""
    return "(" + ", ".join(f"'{check_month(m)}'" for m in months) + ")"


def unload(select: str, bucket: str, prefix: str) -> str:
    """An UNLOAD of `select` to Parquet; quotes inside the query are doubled, as UNLOAD requires."""
    return f"unload ('{select.replace(chr(39), chr(39) * 2)}') to 's3://{bucket}/{prefix}' iam_role default format as parquet allowoverwrite"


def gate(metrics: dict) -> tuple[bool, str]:
    """Does this evaluation clear the promotion gate? Fails closed while the decision is unmade."""
    if MARGIN is None or LATENCY_BUDGET_MS is None:
        return False, "the promotion margin or latency budget is unset (decision 010), so nothing is promoted"
    mae, champion, p95 = float(metrics["mae_min"]), float(metrics["champion_mae_min"]), float(metrics["p95_latency_ms"])
    needed = champion * (1 - MARGIN)
    if mae > needed:
        return False, f"MAE {mae:.3f} min does not beat {champion:.3f} min by {MARGIN:.0%} (needs <= {needed:.3f})"
    if p95 > LATENCY_BUDGET_MS:
        return False, f"p95 latency {p95:.1f} ms is over the {LATENCY_BUDGET_MS:.1f} ms budget"
    return True, f"MAE {mae:.3f} min beats {champion:.3f} min by {1 - mae / champion:.1%}; p95 {p95:.1f} ms within {LATENCY_BUDGET_MS:.1f} ms"


@dg.asset(deps=["trip_features"], group_name="model", kinds={"redshift", "s3"}, pool="redshift")
def training_set(redshift: RedshiftData, artifacts: ArtifactBucket) -> dg.MaterializeResult:
    months = [r["trip_month"] for r in redshift.query(f"select distinct trip_month from {STAGING} order by 1")]
    window, holdout = plan_window(months)
    base = f"training/{window[0]}..{window[-1]}__{holdout}"
    train_prefix, holdout_prefix, distance_prefix = f"{base}/train/", f"{base}/holdout/", f"{base}/distances/"
    cols = "pickup_zone_id, dropoff_zone_id, pickup_hour, pickup_dow, duration_min"
    redshift.execute(
        unload(f"select {cols} from {STAGING} where trip_month in {in_list(window)}", artifacts.bucket, train_prefix),
        unload(f"select {cols} from {STAGING} where trip_month = '{holdout}'", artifacts.bucket, holdout_prefix),
        # est_distance_miles: the zone pair's median distance over the window only (never the holdout's).
        unload(
            "select pickup_zone_id, dropoff_zone_id, median(trip_distance) as est_distance_miles from staging.stg_trips "
            f"where trip_month in {in_list(window)} and trip_distance > 0 and duration_min > 0 and duration_min <= 180 "
            "and pickup_zone_id between 1 and 263 and dropoff_zone_id between 1 and 263 group by 1, 2",
            artifacts.bucket,
            distance_prefix,
        ),
    )
    counts = redshift.query(
        f"select (select count(*) from {STAGING} where trip_month in {in_list(window)}) as train, "
        f"(select count(*) from {STAGING} where trip_month = '{holdout}') as holdout"
    )[0]
    return dg.MaterializeResult(
        metadata={
            "training_window": f"{window[0]}..{window[-1]}",
            "holdout_month": holdout,
            "train_prefix": train_prefix,
            "holdout_prefix": holdout_prefix,
            "distance_prefix": distance_prefix,
            "train_rows": dg.MetadataValue.int(int(counts["train"])),
            "holdout_rows": dg.MetadataValue.int(int(counts["holdout"])),
        }
    )


@dg.asset(deps=["training_set"], group_name="model", kinds={"mlflow", "lightgbm"})
def trained_model(context: dg.AssetExecutionContext, artifacts: ArtifactBucket, tracking: MlflowTracking) -> dg.MaterializeResult:
    ts = last_metadata(context.instance, "training_set")
    train = artifacts.read_parquet(ts["train_prefix"])
    distances = artifacts.read_parquet(ts["distance_prefix"])
    model = LightGBMModel().fit(train, distances)

    contract = mlflow_contract()
    name = contract["registered_model"]
    client = tracking.client()  # sets the tracking URI and the experiment from contracts/mlflow.yaml
    with mlflow.start_run(run_name=f"lightgbm-{ts['training_window']}") as run:
        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=model,
            signature=model_signature(),
            code_paths=[str(Path(tripmodel.__file__).parent)],  # serving loads this without pipeline code
            registered_model_name=name,
        )
        mlflow.log_params({"training_window": ts["training_window"], "holdout_month": ts["holdout_month"], **model.params})
    version = str(info.registered_model_version)
    tags = {
        "training_window": ts["training_window"],
        "holdout_month": ts["holdout_month"],
        "features_version": str(features_contract()["version"]),
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
    }
    for key, value in tags.items():
        client.set_model_version_tag(name, version, key, value)
    return dg.MaterializeResult(metadata={"version": dg.MetadataValue.int(int(version)), "run_id": run.info.run_id, **tags})


def _compared_against(client, name: str, train: pd.DataFrame, distances: pd.DataFrame):
    """(predict function, label) for what the new version must beat: the champion, else the v1 baseline."""
    try:
        champion = client.get_model_version_by_alias(name, "champion")
        return mlflow.pyfunc.load_model(f"models:/{name}/{champion.version}").predict, f"champion v{champion.version}"
    except mlflow.exceptions.MlflowException:  # no champion yet
        baseline = BaselineModel().fit(train, distances)
        return (lambda df: baseline.predict(None, df)), "the v1 baseline (no champion yet)"


@dg.asset(deps=["trained_model"], group_name="model", kinds={"mlflow"})
def eval_report(context: dg.AssetExecutionContext, redshift: RedshiftData, artifacts: ArtifactBucket, tracking: MlflowTracking) -> dg.MaterializeResult:
    ts, tm = last_metadata(context.instance, "training_set"), last_metadata(context.instance, "trained_model")
    contract = mlflow_contract()
    name, version = contract["registered_model"], str(tm["version"])
    holdout = artifacts.read_parquet(ts["holdout_prefix"])
    boroughs = {int(r["location_id"]): r["borough"] for r in redshift.query("select location_id, borough from ingest.taxi_zones")}

    client = tracking.client()
    new = evaluate(mlflow.pyfunc.load_model(f"models:/{name}/{version}").predict, holdout, boroughs)
    predict_other, against = _compared_against(client, name, artifacts.read_parquet(ts["train_prefix"]), artifacts.read_parquet(ts["distance_prefix"]))
    other = evaluate(predict_other, holdout, boroughs)

    metrics = {**{k: v for k, v in new.items() if k != "holdout_rows"}, "champion_mae_min": other["mae_min"]}
    with mlflow.start_run(run_id=tm["run_id"]):
        mlflow.log_metrics(metrics)
    for key in ("mae_min", "p95_latency_ms", "champion_mae_min"):
        client.set_model_version_tag(name, version, key, f"{metrics[key]:.6g}")
    return dg.MaterializeResult(
        metadata={
            "version": dg.MetadataValue.int(int(version)),
            "compared_against": against,
            "improvement": dg.MetadataValue.float(1 - new["mae_min"] / other["mae_min"]),
            "mae_min": dg.MetadataValue.float(new["mae_min"]),
            "champion_mae_min": dg.MetadataValue.float(other["mae_min"]),
            "p95_latency_ms": dg.MetadataValue.float(new["p95_latency_ms"]),
            "baseline_p95_latency_ms": dg.MetadataValue.float(other["p95_latency_ms"]),
            **{k: dg.MetadataValue.float(v) for k, v in new.items() if k.startswith("mae_min.")},
        }
    )


@dg.asset_check(asset=eval_report, blocking=True, name="beats_champion", description="The new version beats the champion (or the v1 baseline) by the promotion margin, within the latency budget.")
def beats_champion(context: dg.AssetCheckExecutionContext) -> dg.AssetCheckResult:
    metrics = last_metadata(context.instance, "eval_report")
    passed, reason = gate(metrics)
    return dg.AssetCheckResult(
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        description=reason,
        metadata={k: dg.MetadataValue.float(float(metrics[k])) for k in ("mae_min", "champion_mae_min", "p95_latency_ms") if k in metrics},
    )


@dg.asset(deps=["eval_report"], group_name="model", kinds={"mlflow"})
def challenger(context: dg.AssetExecutionContext, tracking: MlflowTracking) -> dg.MaterializeResult:
    """Set the `challenger` alias, only behind the gate. Never `champion`: S3's approval-gated workflow owns that."""
    metrics = last_metadata(context.instance, "eval_report")
    passed, reason = gate(metrics)  # the same rule again, so running this asset alone can't skip the check
    if not passed:
        raise dg.Failure(f"not promoting: {reason}")
    name, version = mlflow_contract()["registered_model"], str(metrics["version"])
    tracking.client().set_registered_model_alias(name, "challenger", version)
    return dg.MaterializeResult(metadata={"version": dg.MetadataValue.int(int(version)), "alias": "challenger", "reason": reason})


train_model = dg.define_asset_job("train_model", selection=dg.AssetSelection.assets("training_set", "trained_model", "eval_report", "challenger"))
