"""S1 tests: the schema check's strict rule and the S3 sensor. No AWS: fakes from tests/fakes.py."""

import dagster as dg
from fakes import fake_bucket, schema_2023, schema_2025

from labpipes.contracts import Column, trips_schema
from labpipes.defs.schema_check import compare_schema
from labpipes.defs.sensor import trip_files_sensor
from labpipes.partitions import trip_months


def test_contract_schema_matches_itself():
    assert compare_schema(trips_schema(), trips_schema()) == []


def test_airport_fee_casing_is_not_a_mismatch():
    lower = [Column("airport_fee" if c.name == "Airport_fee" else c.name, c.type) for c in trips_schema()]
    assert compare_schema(trips_schema(), lower) == []


def test_an_extra_trailing_column_blocks():
    problems = compare_schema(trips_schema(), schema_2025())
    assert any("column count" in p for p in problems) and any("cbd_congestion_fee" in p for p in problems)


def test_a_missing_column_blocks():
    assert any("missing Airport_fee" in p for p in compare_schema(trips_schema(), trips_schema()[:-1]))


def test_changed_types_block():
    problems = compare_schema(trips_schema(), schema_2023())
    assert any("VendorID" in p and "INT64" in p for p in problems)
    assert any("passenger_count" in p and "DOUBLE" in p for p in problems)


def test_swapped_same_type_columns_block():
    cols = list(trips_schema())
    cols[7], cols[8] = cols[8], cols[7]  # PULocationID and DOLocationID are both INT32
    assert len(compare_schema(trips_schema(), cols)) == 2


def test_sensor_requests_new_months_once():
    instance = dg.DagsterInstance.ephemeral()
    bucket = fake_bucket({"2024-01": ("a", trips_schema()), "2024-02": ("b", trips_schema())})
    first = trip_files_sensor(dg.build_sensor_context(instance=instance, resources={"data_bucket": bucket}))
    assert [r.partition_key for r in first.run_requests] == ["2024-01", "2024-02"]
    assert first.dynamic_partitions_requests[0].partition_keys == ["2024-01", "2024-02"]

    instance.add_dynamic_partitions(trip_months.name, ["2024-01", "2024-02"])
    again = trip_files_sensor(dg.build_sensor_context(instance=instance, cursor=first.cursor, resources={"data_bucket": bucket}))
    assert isinstance(again, dg.SkipReason)


def test_sensor_reruns_a_replaced_file_without_a_new_partition():
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, ["2024-01"])
    old = '{"2024-01": "a"}'
    bucket = fake_bucket({"2024-01": ("a2", trips_schema())})
    result = trip_files_sensor(dg.build_sensor_context(instance=instance, cursor=old, resources={"data_bucket": bucket}))
    assert [r.partition_key for r in result.run_requests] == ["2024-01"]
    assert result.run_requests[0].run_key == "2024-01:a2"
    assert not result.dynamic_partitions_requests


def _materialize_raw_trips(month, redshift, bucket):
    from labpipes.defs.raw_trips import raw_trips

    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, [month])
    return dg.materialize([raw_trips], partition_key=month, instance=instance, resources={"redshift": redshift, "data_bucket": bucket})


def test_raw_trips_reloads_a_month_in_one_idempotent_transaction():
    from fakes import LOG, fake_redshift

    result = _materialize_raw_trips("2024-03", fake_redshift(), fake_bucket({"2024-03": ("a", trips_schema())}))
    assert result.success
    sqls = [s for s in LOG if s.startswith(("create", "copy", "delete", "insert"))]
    kinds = [s.split()[0] + " " + s.split()[1] for s in sqls]
    assert kinds.index("delete from") < kinds.index("insert into")
    assert kinds.index("copy landing") < kinds.index("delete from")
    assert any("where source_file = 'yellow_tripdata_2024-03.parquet'" in s for s in sqls if s.startswith("delete"))
    copy = next(s for s in sqls if s.startswith("copy"))
    assert "iam_role default" in copy and "format as parquet" in copy
    assert "s3://data/trips/yellow_tripdata_2024-03.parquet" in copy
    assert any("diststyle even sortkey (source_file)" in s for s in sqls)


def test_raw_trips_rejects_a_bad_month_before_any_sql():
    import pytest
    from fakes import LOG, fake_redshift

    from labpipes.defs.raw_trips import raw_trips

    with pytest.raises(ValueError):
        raw_trips(dg.build_asset_context(partition_key="2024-03'; drop table x; --"), fake_redshift(), fake_bucket({}))
    assert not any("drop table" in s for s in LOG)


def test_mcp_user_is_created_once_and_only_granted_marts_select():
    from labpipes.defs.marts import MCP_DB_USER, mcp_user_statements

    first = mcp_user_statements("lab_ro", exists=False)
    again = mcp_user_statements("lab_ro", exists=True)
    assert first[0] == "create user lab_ro password disable" and first[1:] == again
    joined = " ".join(first).lower()
    assert "grant select on marts.zone_hour_stats to lab_ro" in joined and "usage on schema marts" in joined
    grants = [st.lower() for st in first if st.lower().startswith(("grant", "alter default"))]
    assert not any(w in " ".join(grants) for w in ("ingest", "staging", "insert", "update", "delete", "all privileges", "create"))
    assert "revoke create on schema public from public" in joined  # PUBLIC could create tables in schema public
    assert MCP_DB_USER == "lab_ro"
    for bad in ("labadmin", "x; drop table marts.zone_hour_stats", "Lab_RO", ""):
        try:
            mcp_user_statements(bad, exists=False)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")
