"""S2 feature_contract: names, types and ranges, and that a leaked column is reported by name."""

from labpipes.contracts import features_contract
from labpipes.defs.feature_check import compare_columns, expected_columns, range_query


def test_the_contracts_own_columns_match():
    exp = expected_columns(features_contract())
    assert compare_columns(exp, dict(exp), features_contract()["forbidden"]) == []
    assert set(exp) == {"trip_month", "pickup_zone_id", "dropoff_zone_id", "pickup_hour", "pickup_dow", "duration_min"}


def test_a_leaked_post_trip_column_is_named():
    exp = expected_columns(features_contract())
    problems = compare_columns(exp, {**exp, "trip_distance": "double precision"}, features_contract()["forbidden"])
    assert problems == ["leaked column trip_distance: known only after a trip, never an input"]


def test_extra_missing_and_retyped_columns_block():
    exp = expected_columns(features_contract())
    actual = {k: v for k, v in exp.items() if k != "pickup_hour"}
    actual["pickup_dow"] = "integer"
    actual["surge"] = "double precision"
    problems = compare_columns(exp, actual, features_contract()["forbidden"])
    assert "missing column pickup_hour" in problems and "extra column surge" in problems
    assert any("pickup_dow" in p and "smallint" in p for p in problems)


def test_the_range_query_uses_the_contracts_ranges():
    sql = range_query(features_contract(), "2024-03")
    assert "pickup_zone_id < 1 or pickup_zone_id > 263" in sql
    assert "pickup_hour < 0 or pickup_hour > 23" in sql and "pickup_dow < 0 or pickup_dow > 6" in sql
    assert "duration_min <= 0 or duration_min > 180" in sql and "trip_month = '2024-03'" in sql
