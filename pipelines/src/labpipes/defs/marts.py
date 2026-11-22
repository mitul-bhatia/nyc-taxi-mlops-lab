"""zone_hour_stats: trip counts and duration percentiles per pickup zone and hour, over every month.

It's unpartitioned and rebuilt in full; include it in the ingest run so a month that fails a blocking
check upstream never reaches the mart. The table's DDL is the frozen contract in contracts/marts.sql.
"""

import os
import re

import dagster as dg

from ..contracts import marts_ddl
from ..resources import RedshiftData
from ..sql import statements


# The MCP server's database user (S7, decision 021): it reads marts and nothing else. The user is created
# without a password (the Data API signs in as it with temporary credentials), and the grants are re-asserted
# after every refresh, so a mart that is ever dropped and recreated can't quietly lose or widen them.
MCP_DB_USER = os.environ.get("MCP_DB_USER", "lab_ro")


def mcp_user_statements(user: str, exists: bool) -> list[str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", user) or user in ("labadmin", "awsuser", "root"):
        raise ValueError(f"not a valid read-only user name: {user!r}")
    return ([] if exists else [f"create user {user} password disable"]) + [
        f"grant usage on schema marts to {user}",
        f"grant select on marts.zone_hour_stats to {user}",
        f"alter default privileges in schema marts grant select on tables to {user}",
        # Every Redshift user is in the PUBLIC group, which may create tables in schema public by default:
        # that would be a place for the "read-only" user to write. Only labadmin (a superuser) uses public.
        "revoke create on schema public from public",
    ]


@dg.asset(deps=["stg_trips", "taxi_zones"], group_name="marts", kinds={"redshift"}, pool="redshift")
def zone_hour_stats(redshift: RedshiftData) -> dg.MaterializeResult:
    redshift.execute("create schema if not exists marts", *marts_ddl(), *statements("zone_hour_stats"))
    exists = int(redshift.scalar(f"select count(*) from pg_user where usename = '{MCP_DB_USER}'")) > 0
    for statement in mcp_user_statements(MCP_DB_USER, exists):  # one at a time: CREATE USER can't share a batch
        redshift.execute(statement)
    s = redshift.query(
        "select count(*) as rows, count(distinct pickup_zone_id) as zones, min(first_month) as first_month, "
        "max(last_month) as last_month from marts.zone_hour_stats"
    )[0]
    return dg.MaterializeResult(
        metadata={
            "rows": dg.MetadataValue.int(int(s["rows"])),
            "zones": dg.MetadataValue.int(int(s["zones"])),
            "months": f"{s['first_month']}..{s['last_month']}",
        }
    )
