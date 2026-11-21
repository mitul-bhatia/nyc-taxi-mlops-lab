"""S2: the feature_contract check on trip_features.

contracts/features.yaml lists the model's inputs, label and the post-trip columns that must never be
inputs; contracts/mlflow.yaml says this check blocks. A failure stops training before it starts.

Decision (decisions/011-feature-contract-rule.md): "matches" means all three of
  1. names:  the table has exactly the contract's columns (keys, inputs, label): none missing, none extra,
             and none of the contract's `forbidden` post-trip columns (a leaked column is reported by name);
  2. types:  each column has the contract's type;
  3. ranges: for the month, no null input or label and no value outside the contract's ranges (zones 1..263,
             hour 0..23, day of week 0..6, duration above 0 and up to 180 minutes).
"""

import re

import dagster as dg

from ..contracts import features_contract
from ..partitions import check_month, trip_months
from ..resources import RedshiftData
from .features import trip_features

IDENT = re.compile(r"[a-z_][a-z0-9_]*")
# The contract writes types the way DDL does; information_schema.columns spells them differently.
TYPE_NAMES = {"char(7)": "character", "integer": "integer", "smallint": "smallint", "double precision": "double precision"}


def expected_columns(contract: dict) -> dict[str, str]:
    """Column name -> information_schema data_type: the keys, the inputs and the label."""
    cols = [*contract["keys"], *contract["inputs"], contract["label"]]
    return {c["name"]: TYPE_NAMES.get(c["type"], c["type"]) for c in cols}


def compare_columns(expected: dict[str, str], actual: dict[str, str], forbidden: list[str]) -> list[str]:
    """Every way the table's columns differ from the contract; an empty list means they match."""
    problems = [f"leaked column {c}: known only after a trip, never an input" for c in actual if c in forbidden]
    problems += [f"extra column {c}" for c in actual if c not in expected and c not in forbidden]
    problems += [f"missing column {c}" for c in expected if c not in actual]
    problems += [f"column {c}: type is {actual[c]}, contract says {t}" for c, t in expected.items() if c in actual and actual[c] != t]
    return problems


def range_query(contract: dict, month: str) -> str:
    """One query counting, for the month, the rows that are null or out of range in any input or the label."""
    conds = []
    for col in contract["inputs"]:
        lo, hi = col["range"]
        conds.append(f"{col['name']} is null or {col['name']} < {lo} or {col['name']} > {hi}")
    lab = contract["label"]
    lo, hi = lab["range"]
    low = f"{lab['name']} <= {lo}" if lab.get("exclusive_min") else f"{lab['name']} < {lo}"
    conds.append(f"{lab['name']} is null or {low} or {lab['name']} > {hi}")
    for name in [c["name"] for c in contract["inputs"]] + [lab["name"]]:
        assert IDENT.fullmatch(name), name  # names come from the frozen contract; never from a caller
    bad = " or ".join(f"({c})" for c in conds)
    return (
        f"select count(*) as total, coalesce(sum(case when {bad} then 1 else 0 end), 0) as bad "
        f"from staging.trip_features where trip_month = '{check_month(month)}'"
    )


@dg.asset_check(
    asset=trip_features,
    partitions_def=trip_months,
    blocking=True,
    name="feature_contract",
    description="staging.trip_features has exactly the contract's columns and types, and every value is in range.",
)
def feature_contract(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    contract = features_contract()
    table_schema, table_name = contract["table"].split(".")
    rows = redshift.query(
        "select column_name, data_type from information_schema.columns "
        f"where table_schema = '{table_schema}' and table_name = '{table_name}'"
    )
    problems = compare_columns(expected_columns(contract), {r["column_name"]: r["data_type"] for r in rows}, contract["forbidden"])
    bad = total = 0
    if not problems:  # ranges only make sense on a table with the right columns
        row = redshift.query(range_query(contract, context.partition_key))[0]
        total, bad = int(row["total"]), int(row["bad"])
        if total == 0:
            problems.append("the month has no rows")
        if bad:
            problems.append(f"{bad:,} of {total:,} rows have a null or out-of-range input or label")
    return dg.AssetCheckResult(
        passed=not problems,
        severity=dg.AssetCheckSeverity.ERROR,
        description="matches the contract" if not problems else "; ".join(problems[:5]),
        metadata={"problems": dg.MetadataValue.json(problems), "rows": dg.MetadataValue.int(total), "bad_rows": dg.MetadataValue.int(bad)},
    )
