"""S1 quality checks: the thresholds' logic, and that blocking checks stop the mart but warnings don't."""

import dagster as dg
from fakes import FakeRedshift, fake_bucket

from labpipes.contracts import trips_schema
from labpipes.defs.feature_check import feature_contract
from labpipes.defs.features import trip_features
from labpipes.defs.marts import zone_hour_stats
from labpipes.defs.quality import ROW_BOUNDS, SHARE_RULES, judge, no_duplicates, raw_staging_parity, row_count_bounds, share_checks
from labpipes.defs.raw_trips import raw_trips
from labpipes.defs.schema_check import schema_contract
from labpipes.defs.sensor import ingest_month
from labpipes.defs.staging import stg_trips
from labpipes.defs.trip_files import trip_files
from labpipes.defs.zones import taxi_zones
from labpipes.partitions import trip_months

MONTH = "2024-03"
TOTAL = 3_000_000


def test_judge_uses_the_share_and_never_passes_an_empty_month():
    assert judge(0, 100, 0.0)[0] and not judge(1, 100, 0.0)[0]
    assert judge(10_000, 1_000_000, 0.01)[0] and not judge(10_001, 1_000_000, 0.01)[0]
    assert judge(0, 0, 1.0) == (False, "the month has no rows")


def test_todays_real_months_pass_every_threshold():
    worst = {"key_columns_not_null": 0, "duration_plausible": 0.0010, "unknown_zones": 0.0106, "distance_positive": 0.0243,
             "fare_non_negative": 0.0164}
    for rule in SHARE_RULES:
        if rule.name in worst:
            assert judge(int(worst[rule.name] * 1_000_000), 1_000_000, rule.max_share)[0], rule.name


def test_march_passenger_nulls_warn_but_january_does_not():
    rule = next(r for r in SHARE_RULES if r.name == "passenger_nulls")
    assert not judge(426_190, 3_582_628, rule.max_share)[0]
    assert judge(140_162, 2_964_624, rule.max_share)[0]
    assert not rule.blocking


def test_row_bounds_cover_todays_months():
    assert ROW_BOUNDS[0] < 2_964_624 and 3_582_628 < ROW_BOUNDS[1]


def test_which_checks_block():
    blocking = {s.key.name for c in [*share_checks, no_duplicates, row_count_bounds, raw_staging_parity] for s in c.check_specs if s.blocking}
    assert blocking == {"key_columns_not_null", "duration_plausible", "no_duplicates", "row_count_bounds", "raw_staging_parity"}


class ScriptedRedshift(FakeRedshift):
    """Answers the quality queries: `bad` rows for the check whose condition appears in the SQL."""

    bad: dict = {}

    def query(self, sql: str) -> list[dict]:
        if "as bad" in sql:
            hit = [n for cond, n in self.bad.items() if cond in sql]
            return [{"bad": hit[0] if hit else 0, "total": TOTAL}]
        if "information_schema.columns" in sql:
            from labpipes.contracts import features_contract
            from labpipes.defs.feature_check import expected_columns

            return [{"column_name": c, "data_type": t} for c, t in expected_columns(features_contract()).items()]
        if "from staging.trip_features" in sql and "as bad" in sql:
            return [{"total": TOTAL, "bad": 0}]
        if "as raw_rows" in sql:
            return [{"raw_rows": TOTAL, "stg_rows": TOTAL}]
        return super().query(sql)


def _run(bad: dict):
    redshift = ScriptedRedshift(cluster_id="lab", database="lab", db_user="labadmin", rows=TOTAL, bad=bad)
    defs = dg.Definitions(
        assets=[trip_files, raw_trips, stg_trips, taxi_zones, zone_hour_stats, trip_features],
        asset_checks=[schema_contract, feature_contract, *share_checks, no_duplicates, row_count_bounds, raw_staging_parity],
        jobs=[ingest_month],
        resources={"redshift": redshift, "data_bucket": fake_bucket({MONTH: ("e", trips_schema())})},
    )
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, [MONTH])
    result = defs.resolve_job_def("ingest_month").execute_in_process(partition_key=MONTH, instance=instance, raise_on_error=False)
    done = {e.asset_key.to_user_string() for e in result.get_asset_materialization_events()}
    return result, done


def test_a_clean_month_reaches_the_mart():
    result, done = _run({})
    assert result.success and "zone_hour_stats" in done


def test_a_blocking_failure_stops_the_mart():
    result, done = _run({"duration_min <= 0": 200_000})  # 6.7% impossible durations
    assert not result.success
    assert "stg_trips" in done and "zone_hour_stats" not in done


def test_a_warning_never_stops_the_mart():
    result, done = _run({"passenger_count is null": 600_000})  # 20% null passengers: warn only
    assert result.success and "zone_hour_stats" in done
    warns = [e for e in result.get_asset_check_evaluations() if not e.passed]
    assert [w.check_name for w in warns] == ["passenger_nulls"]
