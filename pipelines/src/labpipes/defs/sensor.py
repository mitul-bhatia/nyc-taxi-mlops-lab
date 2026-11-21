"""S1: the S3 sensor (and the job it starts).

Watch trips/ in the data bucket (DataBucket.trip_files). When a month's file appears or changes, add
the month to the trip_months partitions and request a run that goes from trip_files to zone_hour_stats and trip_features (S2).

The cursor remembers each month's ETag. A new month, or a month whose file was replaced (new ETag),
gets a run; the run_key includes the ETag, so asking twice for the same file is a no-op. taxi_zones is
in the job because the mart joins it for zone names, and zone_hour_stats (unpartitioned) is rebuilt
from every month after each run. A month that fails the blocking schema_contract never reaches
raw_trips, so it never reaches the mart.
"""

import json

import dagster as dg

from ..partitions import MONTH, trip_months
from ..resources import DataBucket

ingest_month = dg.define_asset_job(
    "ingest_month",
    selection=dg.AssetSelection.assets("trip_files", "raw_trips", "stg_trips", "taxi_zones", "zone_hour_stats", "trip_features"),
)


@dg.sensor(
    name="trip_files_sensor",
    job=ingest_month,
    minimum_interval_seconds=60,
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def trip_files_sensor(context: dg.SensorEvaluationContext, data_bucket: DataBucket):
    seen: dict[str, str] = json.loads(context.cursor or "{}")  # month -> ETag already requested
    found = {f.month: f.etag for f in data_bucket.trip_files() if MONTH.fullmatch(f.month)}
    changed = sorted(m for m, etag in found.items() if seen.get(m) != etag)
    if not changed:
        return dg.SkipReason("no new or changed trip files")
    new = [m for m in changed if not context.instance.has_dynamic_partition(trip_months.name, m)]
    return dg.SensorResult(
        run_requests=[dg.RunRequest(partition_key=m, run_key=f"{m}:{found[m]}") for m in changed],
        dynamic_partitions_requests=[trip_months.build_add_request(new)] if new else [],
        cursor=json.dumps({**seen, **{m: found[m] for m in changed}}),
    )
