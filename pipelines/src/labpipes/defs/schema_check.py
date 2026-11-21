"""S1: the schema_contract check on trip_files.

Compare the landed file's Parquet footer (the DataBucket resource reads it) with
contracts/trips.schema.yaml, before raw_trips copies anything. contracts/platform.yaml says this check
is blocking, so when it fails nothing downstream in the run executes.

Decision (decisions/006-schema-mismatch-rule.md): the rule is strict. Parquet COPY maps columns by
position, so every one of these is a mismatch and blocks the month:
  - a different number of columns (a missing or an extra one, even at the end),
  - a column at some position with a different name (names compare case-insensitively: TLC renamed
    airport_fee to Airport_fee in 2023, and positional COPY never sees names),
  - a column at some position with a different storage type.
"""

import dagster as dg

from ..contracts import Column, trips_schema
from ..partitions import trip_months
from ..resources import DataBucket
from .trip_files import trip_files


def compare_schema(expected: list[Column], actual: list[Column]) -> list[str]:
    """Every way `actual` differs from `expected`, in order; an empty list means they match."""
    problems = []
    if len(actual) != len(expected):
        problems.append(f"column count: file has {len(actual)}, contract has {len(expected)}")
    for i in range(max(len(expected), len(actual))):
        pos = i + 1
        if i >= len(actual):
            problems.append(f"column {pos}: missing {expected[i].name} ({expected[i].type})")
        elif i >= len(expected):
            problems.append(f"column {pos}: unexpected {actual[i].name} ({actual[i].type})")
        else:
            want, got = expected[i], actual[i]
            if want.name.lower() != got.name.lower():
                problems.append(f"column {pos}: name is {got.name}, contract says {want.name}")
            if want.type != got.type:
                problems.append(f"column {pos} ({want.name}): type is {got.type}, contract says {want.type}")
    return problems


@dg.asset_check(
    asset=trip_files,
    partitions_def=trip_months,
    blocking=True,
    name="schema_contract",
    description="The Parquet footer matches contracts/trips.schema.yaml: same columns, order and types.",
)
def schema_contract(context: dg.AssetCheckExecutionContext, data_bucket: DataBucket) -> dg.AssetCheckResult:
    footer = data_bucket.footer(context.partition_key)
    problems = compare_schema(trips_schema(), footer.columns)
    return dg.AssetCheckResult(
        passed=not problems,
        severity=dg.AssetCheckSeverity.ERROR,
        description="matches the contract" if not problems else f"{len(problems)} mismatch(es): " + "; ".join(problems[:5]),
        metadata={
            "mismatches": dg.MetadataValue.json(problems),
            "file_columns": dg.MetadataValue.int(len(footer.columns)),
            "contract_columns": dg.MetadataValue.int(len(trips_schema())),
        },
    )
