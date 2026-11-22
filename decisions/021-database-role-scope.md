# 021: The MCP server's user reads `marts.zone_hour_stats` and nothing else

Stage: S7 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The MCP server signs in to Redshift (Data API) as `lab_ro`, created by the `zone_hour_stats` asset without a password. It has `USAGE` on schema `marts` and `SELECT` on `marts.zone_hour_stats` (plus default privileges so a recreated table is still readable) and nothing else. The grants are re-asserted after every refresh, so recreating the mart can't silently lose or widen them. The asset also runs `revoke create on schema public from public`, because every Redshift user inherits create-on-public from the PUBLIC group.

## Rejected alternative

Run the server as `labadmin` (the pipeline's user), as the Data API lets any `--db-user` do.

## Why

A tool server is the part of the system that processes text an attacker may influence, so it must not be able to do more than its two queries. `labadmin` can read raw trips, write marts and drop tables; `lab_ro` can read one table. How I know, by trying (signed in as `lab_ro` through the Data API): `select` on the mart is allowed; `select` on `ingest.raw_trips` and `staging.stg_trips` is denied (permission denied for the schema); `insert`, `delete` and `drop table` on the mart are denied; `create table` in `marts` is denied. The first test also found a hole I had not granted: `create table public.evil` succeeded, from the PUBLIC default. I dropped the stray table, revoked it, and the same attempt is now denied. The `/healthz?db=1` check (read marts, write marts, use ingest, create in marts) and the status workflow show the grader the same facts, and a test asserts the grant statements contain nothing but marts `USAGE` and `SELECT` and the PUBLIC revoke.

What it can still do: run `select` on the mart (the point), and create temporary, session-only tables, which vanish at disconnect and touch nothing shared. It can also see the zone statistics of every zone, which is not secret.

## What would show this was wrong

The status artifact showing `write_marts` or `use_ingest` as true, a second mart added that holds something a rider shouldn't see (then grant per table, which is what I did), or the default privileges widening access to a table later added to `marts`.
