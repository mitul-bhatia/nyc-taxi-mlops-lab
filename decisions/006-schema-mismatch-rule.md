# 006: Any difference between a file's schema and the contract blocks the month

Stage: S1 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The blocking `schema_contract` check compares each file's Parquet footer with `contracts/trips.schema.yaml` strictly. A different column count (missing or extra, even a trailing one), a different name at a position (names compare case-insensitively), or a different storage type at a position is a mismatch. Nothing is copied for that month.

## Rejected alternative

Ignore names and only compare column count and types, or tolerate extra trailing columns.

## Why

Parquet COPY maps columns by position, so a schema that moved can load the wrong values into the wrong columns without any error. Comparing only counts and types would let two same-typed columns that swapped (for example pickup and dropoff zone) load silently wrong. Tolerating extra trailing columns would hide that the source changed. The cost: a harmless new column stops the month until I evolve the contract on purpose.

## What would show this was wrong

Frequent blocks caused by harmless changes (extra columns I would have wanted loaded), or a month that loaded fine but turned out to have shifted columns (which would mean the check missed something).
