"""S6: the wiki RAG branch, in the `rag` database on RDS (pgvector), on a daily schedule.

    wiki_pages  ->  wiki_chunks  ->  wiki_embeddings  (+ blocking check wiki_complete)

- wiki_pages: the published pages from the ops wiki (resource `wiki`: labpipes/wiki.py), by conditional GET.
  Each page's ETag is stored and sent next time: an unchanged page costs a 304 and no body.
- wiki_chunks: pages whose chunks are out of date (chunked_etag != etag) are split by the chat model,
  which only proposes paragraph boundaries (decision 018, labpipes/agentic_chunking.py); a failed model
  answer falls back to the fixed chunker. A chunk's id is the content hash, so unchanged text keeps its id
  and its vector.
- wiki_embeddings: only chunks with no vector yet are embedded (resource `embed`, nomic-embed-text,
  768 dimensions, "search_document: " prefix added by the resource), under an HNSW index (decision 019).
- wiki_complete (blocking): every page has chunks and every chunk has a vector.
The job `ingest_wiki` runs the three; the schedule runs it daily. A second run with nothing changed fetches
nothing, chunks nothing and embeds nothing, and says so in each asset's metadata.
"""

import time

import dagster as dg

from ..agentic_chunking import chunk_page
from ..resources import LlmChat, LlmEmbed, RagStore, WikiApi

DDL = (
    "create extension if not exists vector",
    "create schema if not exists rag",
    """create table if not exists rag.pages (
         id text primary key, title text not null, etag text not null, updated_at text, body text not null,
         chunked_etag text, fetched_at timestamptz not null default now())""",
    """create table if not exists rag.chunks (
         id text primary key, page_id text not null references rag.pages(id) on delete cascade,
         idx int not null, heading text not null, content text not null, tokens int not null,
         method text not null, embedding vector(768))""",
    "create index if not exists chunks_page on rag.chunks (page_id)",
    # HNSW (decision 019): needs no training data, so it builds on an empty table and stays accurate as
    # chunks come and go. vector_cosine_ops: the app orders by cosine distance (<=>).
    "create index if not exists chunks_embedding_hnsw on rag.chunks using hnsw (embedding vector_cosine_ops)",
)


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def connect(rag: RagStore):
    conn = rag.connect()
    with conn, conn.cursor() as cur:
        for sql in DDL:
            cur.execute(sql)
    return conn


@dg.asset(group_name="rag", kinds={"postgres"}, description="The wiki's published pages, by conditional GET.")
def wiki_pages(context: dg.AssetExecutionContext, wiki: WikiApi, rag: RagStore) -> dg.MaterializeResult:
    client = wiki.client()
    conn = connect(rag)
    with conn, conn.cursor() as cur:
        cur.execute("select id, etag from rag.pages")
        stored = dict(cur.fetchall())
        refs = client.pages()
        fetched = unchanged = 0
        for ref in refs:
            page = client.page(ref.id, stored.get(ref.id))
            if page is None:  # 304: nothing changed, no body sent
                unchanged += 1
                continue
            fetched += 1
            cur.execute(
                """insert into rag.pages (id, title, etag, updated_at, body) values (%s, %s, %s, %s, %s)
                   on conflict (id) do update set title = excluded.title, etag = excluded.etag,
                     updated_at = excluded.updated_at, body = excluded.body, fetched_at = now()""",
                (page.id, page.title, page.etag, page.updated_at, page.body),
            )
        gone = sorted(set(stored) - {r.id for r in refs})
        for page_id in gone:  # unpublished pages: their chunks go with them (on delete cascade)
            cur.execute("delete from rag.pages where id = %s", (page_id,))
    conn.close()
    context.log.info("%d listed, %d fetched, %d unchanged (304), %d removed", len(refs), fetched, unchanged, len(gone))
    return dg.MaterializeResult(metadata={
        "pages_listed": len(refs), "pages_fetched": fetched, "pages_unchanged_304": unchanged, "pages_removed": len(gone)})


@dg.asset(deps=[wiki_pages], group_name="rag", kinds={"postgres"},
          description="Chunks of every page whose chunks are out of date, split by the chat model (decision 018).")
def wiki_chunks(context: dg.AssetExecutionContext, chat: LlmChat, rag: RagStore) -> dg.MaterializeResult:
    conn = connect(rag)
    pages_done = written = llm_pages = fallback_pages = 0
    seconds = 0.0
    reasons: list[str] = []
    with conn, conn.cursor() as cur:
        cur.execute("select id, title, body, etag from rag.pages where chunked_etag is distinct from etag order by id")
        todo = cur.fetchall()
        for page_id, title, body, etag in todo:
            t0 = time.perf_counter()
            result = chunk_page(title, body, chat.complete_json)
            seconds += time.perf_counter() - t0
            ids = [c.content_hash(page_id) for c in result.chunks]
            cur.execute("delete from rag.chunks where page_id = %s and id <> all(%s)", (page_id, ids))
            for chunk, cid in zip(result.chunks, ids, strict=True):
                cur.execute(
                    """insert into rag.chunks (id, page_id, idx, heading, content, tokens, method) values (%s, %s, %s, %s, %s, %s, %s)
                       on conflict (id) do update set idx = excluded.idx, heading = excluded.heading, method = excluded.method""",
                    (cid, page_id, chunk.index, chunk.heading, chunk.text, chunk.tokens, result.method),
                )
            cur.execute("update rag.pages set chunked_etag = %s where id = %s", (etag, page_id))
            pages_done += 1
            written += len(ids)
            if result.method == "llm":
                llm_pages += 1
            else:
                fallback_pages += 1
                reasons.append(f"{page_id}: {result.reason}")
            context.log.info("%s: %d chunks by %s", page_id, len(ids), result.method)
        cur.execute("select count(*) from rag.chunks")
        total = cur.fetchone()[0]
    conn.close()
    return dg.MaterializeResult(metadata={
        "pages_chunked": pages_done, "chunks_written": written, "chunks_total": int(total), "pages_by_llm": llm_pages,
        "pages_by_fallback": fallback_pages, "chunking_seconds": round(seconds, 1), "fallback_reasons": reasons[:10]})


@dg.asset(deps=[wiki_chunks], group_name="rag", kinds={"postgres"}, description="A vector for every chunk that has none.")
def wiki_embeddings(context: dg.AssetExecutionContext, embed: LlmEmbed, rag: RagStore) -> dg.MaterializeResult:
    conn = connect(rag)
    with conn, conn.cursor() as cur:
        cur.execute("select id, content from rag.chunks where embedding is null order by id")
        todo = cur.fetchall()
        vectors = embed.embed_documents([content for _, content in todo]) if todo else []
        for (cid, _), v in zip(todo, vectors, strict=True):
            cur.execute("update rag.chunks set embedding = %s::vector where id = %s", (vector_literal(v), cid))
        cur.execute("select count(*) from rag.chunks")
        total = cur.fetchone()[0]
    conn.close()
    return dg.MaterializeResult(metadata={"chunks_embedded": len(todo), "chunks_total": int(total)})


@dg.asset_check(asset=wiki_embeddings, blocking=True, description="Every page has chunks and every chunk has a vector.")
def wiki_complete(rag: RagStore) -> dg.AssetCheckResult:
    conn = connect(rag)
    with conn, conn.cursor() as cur:
        cur.execute("select count(*) from rag.chunks")
        chunks = cur.fetchone()[0]
        cur.execute("select count(*) from rag.chunks where embedding is null")
        no_vector = cur.fetchone()[0]
        cur.execute("select count(*) from rag.pages p where not exists (select 1 from rag.chunks c where c.page_id = p.id)")
        no_chunks = cur.fetchone()[0]
        cur.execute("select count(*) from rag.pages")
        pages = cur.fetchone()[0]
    conn.close()
    return dg.AssetCheckResult(
        passed=chunks > 0 and no_vector == 0 and no_chunks == 0,
        severity=dg.AssetCheckSeverity.ERROR,
        description=f"{pages} pages, {chunks} chunks, {no_vector} without a vector, {no_chunks} pages without chunks",
        metadata={"pages": dg.MetadataValue.int(pages), "chunks": dg.MetadataValue.int(chunks),
                  "chunks_without_vector": dg.MetadataValue.int(no_vector), "pages_without_chunks": dg.MetadataValue.int(no_chunks)},
    )


ingest_wiki = dg.define_asset_job("ingest_wiki", selection=dg.AssetSelection.assets(wiki_pages, wiki_chunks, wiki_embeddings))

# The wiki is re-read daily. Unchanged pages cost a 304 each, so a daily run with nothing new is cheap.
ingest_wiki_daily = dg.ScheduleDefinition(job=ingest_wiki, cron_schedule="0 6 * * *", default_status=dg.DefaultScheduleStatus.RUNNING)
