"""S1: the raw_trips asset.

One partition per month (partitions.trip_months), depending on trip_files. COPY the month's file into
Redshift with the LabRole attached to the cluster (`iam_role default`), so that re-running a month never
duplicates its rows. The landing columns come from contracts/trips.schema.yaml
(contracts.redshift_columns).

Decisions: COPY, not Spectrum (decisions/004-copy-vs-spectrum.md); raw_trips is sorted by source_file and
spread EVENly, because every reload and every staging step filters on source_file and one node has no
shuffle for a distribution key to avoid (decisions/005-dist-and-sort-keys.md).

Idempotent: the statements run as one transaction that deletes the month's rows (by source_file) and
loads them again, so a rerun leaves exactly one copy, and a failed COPY rolls the delete back, leaving the
old rows. The file is copied into a temp table first because Parquet COPY maps columns by position and
the table has one more column (source_file) than the file.
"""

import dagster as dg

from ..contracts import redshift_columns, trips_schema
from ..partitions import source_file, trip_months
from ..resources import DataBucket, RedshiftData


@dg.asset(partitions_def=trip_months, deps=["trip_files"], group_name="ingest", kinds={"s3", "redshift"}, pool="redshift")
def raw_trips(context: dg.AssetExecutionContext, redshift: RedshiftData, data_bucket: DataBucket) -> dg.MaterializeResult:
    month = context.partition_key
    file = source_file(month)  # validates the month: it's formatted into SQL below
    uri = data_bucket.trip_uri(month)
    columns = redshift_columns(trips_schema())  # from the frozen contract, not from user input
    redshift.execute(
        "create schema if not exists ingest",
        f"create table if not exists ingest.raw_trips ({columns}, source_file varchar(64) not null) "
        "diststyle even sortkey (source_file)",
        f"create temp table landing ({columns})",
        f"copy landing from '{uri}' iam_role default format as parquet",
        f"delete from ingest.raw_trips where source_file = '{file}'",
        f"insert into ingest.raw_trips select *, '{file}' from landing",
    )
    rows = int(redshift.scalar(f"select count(*) from ingest.raw_trips where source_file = '{file}'"))
    return dg.MaterializeResult(
        metadata={
            "rows": dg.MetadataValue.int(rows),
            "file_rows": dg.MetadataValue.int(data_bucket.footer(month).num_rows),
            "source": uri,
        }
    )
