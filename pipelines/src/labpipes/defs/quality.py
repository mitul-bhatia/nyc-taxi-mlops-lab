"""S1: data-quality checks on stg_trips (nulls, ranges, duplicates, row counts).

Decision 007 (decisions/007-quality-checks.md). The numbers behind it are what real months look like in
Redshift (2024-01..04, about 3M trips each): null key columns 0; duration <= 0 or > 180 min 0.09-0.10%;
exact duplicates 0-1 rows; unknown zone ids 0.96-1.06%; trip_distance <= 0 1.3-2.4%; negative fares
1.3-1.6%; null passenger_count 4.7% rising to 11.9%; rows 2.96M-3.58M.

BLOCKING (a failure stops zone_hour_stats in the same run, so the month never reaches the mart):
  key_columns_not_null   no null pickup/dropoff time or zone
  duration_plausible     at most 1% of trips with duration <= 0 or > 180 min (about 10x the worst month)
  no_duplicates          at most 0.1% exact duplicate rows
  row_count_bounds       between 1,000,000 and 6,000,000 rows
  raw_staging_parity     staging has exactly the rows raw_trips loaded for the month
WARNING (reported, never blocks):
  unknown_zones          over 2% of trips with a zone id outside 1..263
  distance_positive      over 5% of trips with trip_distance <= 0
  fare_non_negative      over 5% of trips with a negative fare
  passenger_nulls        over 10% null passenger_count (not a model input; a signal the source changed)
"""

from dataclasses import dataclass

import dagster as dg

from ..partitions import check_month, source_file, trip_months
from ..resources import RedshiftData
from .staging import stg_trips

ROW_BOUNDS = (1_000_000, 6_000_000)


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    blocking: bool
    max_share: float  # the largest share of bad rows that still passes
    where: str  # SQL condition that marks a bad row (it is a constant of this module, never user input)


SHARE_RULES = [
    Rule("key_columns_not_null", "No null pickup/dropoff time or zone id.", True, 0.0,
         "pickup_at is null or dropoff_at is null or pickup_zone_id is null or dropoff_zone_id is null"),
    Rule("duration_plausible", "At most 1% of trips last <= 0 or > 180 minutes.", True, 0.01,
         "duration_min <= 0 or duration_min > 180"),
    Rule("unknown_zones", "At most 2% of trips use a zone id outside 1..263.", False, 0.02,
         "pickup_zone_id not between 1 and 263 or dropoff_zone_id not between 1 and 263"),
    Rule("distance_positive", "At most 5% of trips have trip_distance <= 0.", False, 0.05, "trip_distance <= 0"),
    Rule("fare_non_negative", "At most 5% of trips have a negative fare.", False, 0.05, "fare_amount < 0"),
    Rule("passenger_nulls", "At most 10% of trips have a null passenger_count.", False, 0.10, "passenger_count is null"),
]


def judge(bad: int, total: int, max_share: float) -> tuple[bool, str]:
    """Pass when the share of bad rows is within the limit. An empty month never passes."""
    if total == 0:
        return False, "the month has no rows"
    share = bad / total
    return share <= max_share, f"{bad:,} of {total:,} rows ({share:.3%}); limit {max_share:.1%}"


def _result(passed: bool, blocking: bool, description: str, **metadata) -> dg.AssetCheckResult:
    return dg.AssetCheckResult(
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR if blocking else dg.AssetCheckSeverity.WARN,
        description=description,
        metadata={k: dg.MetadataValue.float(float(v)) if isinstance(v, float) else dg.MetadataValue.int(int(v)) for k, v in metadata.items()},
    )


def _share_check(rule: Rule):
    @dg.asset_check(asset=stg_trips, partitions_def=trip_months, blocking=rule.blocking, name=rule.name, description=rule.description)
    def check(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
        month = check_month(context.partition_key)
        row = redshift.query(
            f"select coalesce(sum(case when {rule.where} then 1 else 0 end), 0) as bad, count(*) as total "
            f"from staging.stg_trips where trip_month = '{month}'"
        )[0]
        bad, total = int(row["bad"]), int(row["total"])
        passed, description = judge(bad, total, rule.max_share)
        return _result(passed, rule.blocking, description, bad_rows=bad, total_rows=total, share=bad / total if total else 1.0)

    return check


@dg.asset_check(asset=stg_trips, partitions_def=trip_months, blocking=True, name="no_duplicates",
                description="At most 0.1% of trips are exact duplicates (same vendor, times, zones, distance and total).")
def no_duplicates(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    month = check_month(context.partition_key)
    row = redshift.query(
        f"select coalesce(sum(c - 1), 0) as bad, (select count(*) from staging.stg_trips where trip_month = '{month}') as total "
        f"from (select count(*) as c from staging.stg_trips where trip_month = '{month}' "
        "group by vendor_id, pickup_at, dropoff_at, pickup_zone_id, dropoff_zone_id, trip_distance, total_amount "
        "having count(*) > 1) as d"
    )[0]
    bad, total = int(row["bad"]), int(row["total"])
    passed, description = judge(bad, total, 0.001)
    return _result(passed, True, description, bad_rows=bad, total_rows=total)


@dg.asset_check(asset=stg_trips, partitions_def=trip_months, blocking=True, name="row_count_bounds",
                description=f"The month has between {ROW_BOUNDS[0]:,} and {ROW_BOUNDS[1]:,} rows.")
def row_count_bounds(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    month = check_month(context.partition_key)
    total = int(redshift.scalar(f"select count(*) from staging.stg_trips where trip_month = '{month}'"))
    low, high = ROW_BOUNDS
    return _result(low <= total <= high, True, f"{total:,} rows; allowed {low:,} to {high:,}", rows=total)


@dg.asset_check(asset=stg_trips, partitions_def=trip_months, blocking=True, name="raw_staging_parity",
                description="Staging has exactly the rows raw_trips loaded for the month.")
def raw_staging_parity(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    month = check_month(context.partition_key)
    row = redshift.query(
        f"select (select count(*) from ingest.raw_trips where source_file = '{source_file(month)}') as raw_rows, "
        f"(select count(*) from staging.stg_trips where trip_month = '{month}') as stg_rows"
    )[0]
    raw, stg = int(row["raw_rows"]), int(row["stg_rows"])
    return _result(raw == stg, True, f"raw {raw:,} rows, staging {stg:,} rows", raw_rows=raw, stg_rows=stg)


share_checks = [_share_check(r) for r in SHARE_RULES]
