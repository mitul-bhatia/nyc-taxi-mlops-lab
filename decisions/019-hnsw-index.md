# 019: HNSW on the chunk vectors (pgvector, cosine)

Stage: S6 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

`rag.chunks.embedding vector(768)` has an HNSW index with `vector_cosine_ops`, created by the ingest itself (`create index if not exists`). The app orders by `embedding <=> query` and takes the top 3.

## Rejected alternative

IVFFlat.

## Why

HNSW builds a graph and needs no training data, so it can be created on an empty table and stays accurate as chunks are added or removed when a wiki page changes. IVFFlat first groups the vectors into lists from the rows present when it is built, so it needs the table filled first, and its recall drifts as the data changes until it is rebuilt; with 17 rows (what we have) its lists would be meaningless, and the ingest creates the index before any data exists. The price of HNSW is more memory and a slower build and insert, which is nothing at 17 chunks. At a million chunks I would still pick HNSW: IVFFlat builds faster and is smaller, but needs a rebuild whenever the data drifts, and an index that quietly loses recall is worse than a bigger one. At this size either would be exact anyway (a sequential scan of 17 rows is instant), so the choice is about how it behaves as the wiki grows.

## What would show this was wrong

Index memory or build time hurting RDS (a `db.t4g.medium`), inserts slowing the daily run, or recall that tests worse than an exact scan on a bigger wiki (then raise `ef_search`, or at a very large scale test IVFFlat against the memory cost).
