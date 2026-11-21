# 005: raw_trips is sorted by source_file and spread EVEN

Stage: S1 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

`ingest.raw_trips` uses `diststyle even sortkey (source_file)`. `staging.stg_trips` keeps the given `sortkey (pickup_at)`.

## Rejected alternative

`sortkey (tpep_pickup_datetime)` with `diststyle auto`, and also `distkey (pulocationid)`.

## Why

Every reload deletes one month by `source_file`, and staging reads one month by `source_file`, so sorting on it lets Redshift skip the blocks of other months. A pickup-time sort key would help time-range queries, but raw_trips is only read per file. A distribution key only avoids network shuffles between nodes, and this cluster has a single ra3.large node, so it would add risk (skew on busy Manhattan zones) for no benefit.

## What would show this was wrong

Slow per-month reloads or staging steps as more months land (look at the scan size in the query plan), a second node being added so that join shuffles show up, or queries on raw_trips that filter on pickup time instead of file.
