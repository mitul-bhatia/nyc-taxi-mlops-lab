"""S2 training: the window, the gate, and the pipeline end to end on fakes and a local MLflow."""

import dagster as dg
import mlflow
import pytest
from fakes import LocalArtifacts, unloading_redshift

from labpipes.contracts import mlflow_contract
from labpipes.defs import training
from labpipes.defs.training import challenger, eval_report, gate, plan_window, trained_model, training_set, unload
from labpipes.resources import MlflowTracking

NAME = mlflow_contract()["registered_model"]
GOOD = {"mae_min": 3.0, "champion_mae_min": 4.0, "p95_latency_ms": 5.0}


def test_the_window_is_the_months_before_the_newest():
    assert plan_window(["2024-04", "2024-01", "2024-03", "2024-02"]) == (["2024-01", "2024-02", "2024-03"], "2024-04")
    assert plan_window(["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]) == (["2024-02", "2024-03", "2024-04"], "2024-05")
    with pytest.raises(dg.Failure):
        plan_window(["2024-01", "2024-02", "2024-03"])  # no month left to test on


def test_unload_doubles_quotes_and_validates_months():
    sql = unload("select 1 where m in ('2024-01')", "b", "p/")
    assert "''2024-01''" in sql and sql.startswith("unload (") and "to 's3://b/p/'" in sql
    with pytest.raises(ValueError):
        training.in_list(["2024-01'; drop table x; --"])


def test_the_decided_gate_is_5_percent_and_10_ms():
    assert (training.MARGIN, training.LATENCY_BUDGET_MS) == (0.05, 10.0)
    assert gate({"mae_min": 3.6355, "champion_mae_min": 3.9563, "p95_latency_ms": 3.04})[0]  # the real S2 numbers: 8.1%, 3 ms
    assert not gate({"mae_min": 3.80, "champion_mae_min": 3.9563, "p95_latency_ms": 3.0})[0]  # only 4%
    assert not gate({"mae_min": 3.0, "champion_mae_min": 4.0, "p95_latency_ms": 10.5})[0]  # accurate but over budget


def test_the_gate_fails_closed_when_a_limit_is_unset(monkeypatch):
    monkeypatch.setattr(training, "MARGIN", None)
    assert not gate(GOOD)[0]
    monkeypatch.setattr(training, "MARGIN", 0.05)
    monkeypatch.setattr(training, "LATENCY_BUDGET_MS", None)
    assert not gate(GOOD)[0]


def test_the_gate_needs_the_margin_and_the_latency_budget(monkeypatch):
    monkeypatch.setattr(training, "MARGIN", 0.10)
    monkeypatch.setattr(training, "LATENCY_BUDGET_MS", 20.0)
    assert gate(GOOD)[0]  # 25% better, 5 ms
    assert not gate({**GOOD, "mae_min": 3.7})[0]  # only 7.5% better
    assert not gate({**GOOD, "p95_latency_ms": 25.0})[0]  # accurate but too slow
    assert not gate({**GOOD, "mae_min": 4.5})[0]  # worse


@pytest.fixture
def stack(tmp_path):
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    mlflow.create_experiment(mlflow_contract()["experiment"], artifact_location=str(tmp_path / "artifacts"))
    root = str(tmp_path / "bucket")
    return {
        "instance": dg.DagsterInstance.ephemeral(),
        "resources": {
            "redshift": unloading_redshift(root),
            "artifacts": LocalArtifacts(bucket="lab", root=root),
            "tracking": MlflowTracking(tracking_uri=f"sqlite:///{tmp_path}/mlflow.db"),
        },
        "client": mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db"),
    }


def _train(stack, *assets):
    return dg.materialize(list(assets or [training_set, trained_model, eval_report]), instance=stack["instance"], resources=stack["resources"], raise_on_error=False)


def _alias(client, alias):
    try:
        return str(client.get_model_version_by_alias(NAME, alias).version)
    except mlflow.exceptions.MlflowException:
        return None


def test_training_registers_a_tagged_version_and_compares_with_the_baseline(stack):
    result = _train(stack)
    assert result.success
    version = stack["client"].get_model_version(NAME, "1")
    for tag in mlflow_contract()["version_tags"]:
        assert tag in version.tags, tag
    assert version.tags["training_window"] == "2024-01..2024-03" and version.tags["holdout_month"] == "2024-04"
    meta = {e.asset_key.to_user_string(): e.materialization.metadata for e in result.get_asset_materialization_events()}
    assert "baseline" in meta["eval_report"]["compared_against"].value
    run = stack["client"].get_run(version.run_id)
    assert {"mae_min", "p95_latency_ms", "champion_mae_min", "mae_min.airport", "mae_min.night"} <= set(run.data.metrics)
    assert _alias(stack["client"], "challenger") is None and _alias(stack["client"], "champion") is None


def test_no_challenger_while_the_gate_is_closed(stack, monkeypatch):
    monkeypatch.setattr(training, "MARGIN", None)
    _train(stack)
    result = _train(stack, challenger)
    assert not result.success
    assert _alias(stack["client"], "challenger") is None


def test_the_challenger_is_set_when_the_gate_passes_and_champion_never(stack, monkeypatch):
    monkeypatch.setattr(training, "MARGIN", 0.01)
    monkeypatch.setattr(training, "LATENCY_BUDGET_MS", 1000.0)
    _train(stack)
    result = _train(stack, challenger)
    assert result.success
    assert _alias(stack["client"], "challenger") == "1" and _alias(stack["client"], "champion") is None


def test_with_a_champion_the_new_version_is_compared_with_it(stack):
    _train(stack)
    stack["client"].set_registered_model_alias(NAME, "champion", "1")  # as S3's promotion workflow would
    result = _train(stack)
    meta = {e.asset_key.to_user_string(): e.materialization.metadata for e in result.get_asset_materialization_events()}
    assert meta["eval_report"]["compared_against"].value == "champion v1"
    assert _alias(stack["client"], "champion") == "1"  # training never moves it
