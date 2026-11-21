# 004: Load each month with COPY instead of querying the files with Spectrum

Stage: S1 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

Each month's Parquet file is loaded into a Redshift table (`ingest.raw_trips`) with `COPY ... iam_role default format as parquet`. Staging and the mart read the loaded table.

## Rejected alternative

Spectrum: leave the files in the instructor's bucket and query them through an external table.

## Why

Loaded data is fast to query, belongs to my warehouse, and a month can be reloaded on its own (delete that month's rows, copy again, in one transaction). With Spectrum there is no load step, but every query reads Parquet from S3 again, I can't choose sort or distribution keys, and every query depends on the instructor's bucket staying available and unchanged. The cost of COPY: it uses cluster storage, and a bad file is fixed by reloading, not by re-querying.

## What would show this was wrong

Storage or load time becoming a problem (the cluster filling up, or a month taking too long to copy), or needing to query months I haven't loaded and don't want to store.
