"""The support assistant: POST /assist (contracts/assistant-openapi.yaml). S6 given, with the retrieval
query and the prompt left to you.

    question -> embed it (llm-embed) -> retrieve wiki chunks (pgvector) -> prompt -> answer (llm-chat)
    S7: the answer may call MCP tools (contracts/mcp-tools.json), as many rounds as MAX_TOOL_ROUNDS

Configuration, all from the app host:
  LLM_HOST                  the LLM host's private IP (runtime.yaml: llm-chat 8080, llm-embed 8081)
  /secrets/rds.json         the RDS connection (lab/rds), copied from Secrets Manager by deploy/web.sh
  /secrets/langfuse.json    tracing (lab/langfuse), optional: see tracing.py
  MCP_URL                   the MCP server (S7: http://mcp:8002/mcp on the lab network)
Until LLM_HOST and the RDS secret are there, /assist answers 503.

The LLM host is slow (a 4-chunk answer takes ~50 s on m8g.large, most of it reading the prompt). Keep the
system prompt fixed and put the retrieved context in the user turn: llama.cpp caches the unchanged
prefix, which saved ~40 s per request in the benchmark.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from mcp import ClientSession

from .tracing import Tracer, now

router = APIRouter()
tracer = Tracer()
log = logging.getLogger("rideapp.assistant")

CHAT_PORT, EMBED_PORT = 8080, 8081
EMBED_MODEL = "nomic-embed-text-v1.5"
RDS_SECRET = Path(os.environ.get("RDS_SECRET_FILE", "/secrets/rds.json"))
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_SECONDS", "150"))
MAX_TOOL_ROUNDS = 3


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    use_wiki: bool = True  # false: no retrieval, to compare answers with and without the wiki


class Source(BaseModel):
    page_id: str
    title: str
    chunk_id: str
    score: float


class ToolCall(BaseModel):
    name: str
    arguments: dict
    ok: bool


class AssistResponse(BaseModel):
    answer: str
    sources: list[Source]
    tool_calls: list[ToolCall]
    model: dict[str, str]
    timings_ms: dict[str, float]
    trace_id: str | None


class Chunk(BaseModel):
    """A retrieved wiki chunk (rag.chunks joined with rag.pages)."""

    id: str
    page_id: str
    title: str
    content: str
    score: float


def llm_host() -> str:
    host = os.environ.get("LLM_HOST", "")
    if not host:
        raise HTTPException(503, "assistant not configured: LLM_HOST is empty (stages/S6.md)")
    return host


async def embed_query(question: str) -> list[float]:
    """The question as a vector. nomic-embed-text wants "search_query: " on queries, "search_document: " on
    the chunks (the pipeline adds that one)."""
    async with httpx2.AsyncClient(timeout=30) as client:
        r = await client.post(f"http://{llm_host()}:{EMBED_PORT}/v1/embeddings",
                              json={"input": [f"search_query: {question}"], "model": "embed"})
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def connect() -> psycopg.AsyncConnection:
    if not RDS_SECRET.exists():
        raise HTTPException(503, f"assistant not configured: no {RDS_SECRET} (deploy/web.sh prepare copies lab/rds)")
    s = json.loads(RDS_SECRET.read_text())
    return await psycopg.AsyncConnection.connect(host=s["host"], port=int(s.get("port", 5432)), user=s["username"],
                                                 password=s["password"], dbname="rag", autocommit=True, connect_timeout=5)


TOP_K = 3  # decision 018: about 100 tokens a chunk, ~8-11 s of prompt time each on the LLM host


async def retrieve(question_vector: list[float]) -> list[Chunk]:
    """The TOP_K chunks closest to the question by cosine distance, best first (rag.chunks, pgvector HNSW
    index: decision 019). The score is cosine similarity, 1 - distance, as the contract says."""
    vec = vector_literal(question_vector)
    async with await connect() as conn:
        cur = await conn.execute(
            """select c.id, c.page_id, p.title, c.content, 1 - (c.embedding <=> %s::vector) as score
               from rag.chunks c join rag.pages p on p.id = c.page_id
               where c.embedding is not null
               order by c.embedding <=> %s::vector limit %s""",
            (vec, vec, TOP_K),
        )
        rows = await cur.fetchall()
    return [Chunk(id=r[0], page_id=r[1], title=r[2], content=r[3], score=float(r[4])) for r in rows]


# Fixed text, so llama.cpp keeps it as a cached prefix: only the excerpts and the question change.
SYSTEM_PROMPT = (
    "You are the support assistant of a New York ride-hailing app. Answer the rider's question using ONLY the "
    "wiki excerpts in their message. If the excerpts do not contain the answer, say that you do not have that "
    "information; never guess and never use outside knowledge. Be concise (two to four sentences) and name the "
    "wiki page you used in parentheses."
)


def build_messages(question: str, chunks: list[Chunk]) -> list[dict]:
    """Fixed system prompt; the retrieved excerpts go in the user turn, before the question."""
    if chunks:
        excerpts = "\n\n".join(f"Excerpt {i} (page: {c.title}):\n{c.content}" for i, c in enumerate(chunks, 1))
    else:
        excerpts = "(no wiki excerpts were found)"
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Wiki excerpts:\n\n{excerpts}\n\nQuestion: {question}"}]


# S7 (decision 020): all three tools are read-only (the MCP server's database user can only read marts), so none
# needs a person to confirm. The model never sees a tool that isn't listed here.
ALLOWED_TOOLS: set[str] = {"get_model_info", "predict_duration", "query_zone_stats"}
TOOLS_PROMPT = (
    "\n\nYou can also call tools, and their results are valid sources like the wiki excerpts. "
    "get_model_info: which model version is serving riders and how accurate it is. "
    "predict_duration: the predicted trip time in minutes between two named NYC taxi zones at an hour and a weekday; "
    "use it whenever the rider asks how long a trip will take. "
    "query_zone_stats: historical trip counts or median / 90th-percentile durations for one pickup zone. "
    "Call a tool only when the rider's question needs it. Pass zone names as TLC zone names, for example "
    "'JFK Airport' or 'Times Sq/Theatre District'. Wiki excerpts answer policy questions; tools answer numbers. "
    "Report a tool's numbers exactly as it returned them."
)
MAX_ZONE_CHARS = 64


def allow_tool_call(name: str, arguments: dict, question: str) -> tuple[bool, str]:
    """Decide whether one tool call the model asked for may run (and what to say if not). Today: the tool must
    be allowed and its text arguments must look like a zone name. (E3 adds the check on where they came from.)"""
    if name not in ALLOWED_TOOLS:
        return False, f"{name} is not an allowed tool"
    for key in ("zone", "pickup_zone", "dropoff_zone"):
        if key in arguments and not (isinstance(arguments[key], str) and 0 < len(arguments[key]) <= MAX_ZONE_CHARS):
            return False, f"{key} must be a zone name of at most {MAX_ZONE_CHARS} characters"
    return True, ""


@asynccontextmanager
async def mcp_session():
    """A session with the MCP server for one /assist request."""
    from mcp import ClientSession  # imported here: only S7's tool path needs the MCP client
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(os.environ["MCP_URL"], timeout=10) as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def tool_definitions(session: ClientSession) -> list[dict]:
    """The MCP server's tools that ALLOWED_TOOLS lets the model see, as OpenAI-style function definitions."""
    tools = (await session.list_tools()).tools
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
            for t in tools if t.name in ALLOWED_TOOLS]


async def call_tool(session: ClientSession, name: str, arguments: dict) -> tuple[bool, str]:
    result = await session.call_tool(name, arguments)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return not result.isError, text


async def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One chat completion from the LLM host. Returns the raw OpenAI-style response."""
    body = {"model": "chat", "messages": messages, "temperature": 0.2, "max_tokens": 300, "cache_prompt": True}
    if tools:
        body["tools"] = tools
    async with httpx2.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
        r = await client.post(f"http://{llm_host()}:{CHAT_PORT}/v1/chat/completions", json=body)
    r.raise_for_status()
    return r.json()


def llm_error(e: BaseException) -> httpx2.HTTPError | None:
    """The LLM host's error inside e, which may arrive wrapped in an ExceptionGroup by the MCP client."""
    if isinstance(e, httpx2.HTTPError):
        return e
    for inner in getattr(e, "exceptions", ()):
        if found := llm_error(inner):
            return found
    return None


async def generate(messages: list[dict], tools: list[dict] | None, timings: dict, trace) -> dict:
    t0, s0 = time.perf_counter(), now()
    reply = await chat(messages, tools)
    timings["generate"] = timings.get("generate", 0) + (time.perf_counter() - t0) * 1000
    if trace:
        msg = reply["choices"][0]["message"]
        trace.generation("chat", s0, reply.get("model", "chat"), messages, msg.get("content") or msg.get("tool_calls"),
                         reply.get("usage"), llama_timings=reply.get("timings"))
    return reply


async def answer_with_tools(question: str, messages: list[dict], session: ClientSession, record: list[ToolCall], timings: dict, trace) -> dict:
    """Chat with the allowed tools: run the calls the model asks for (each through allow_tool_call), give it
    the results, and ask again, at most MAX_TOOL_ROUNDS times."""
    tools = await tool_definitions(session)
    messages[0] = {**messages[0], "content": messages[0]["content"] + TOOLS_PROMPT}  # still a fixed, cacheable prefix
    reply = await generate(messages, tools, timings, trace)
    for _ in range(MAX_TOOL_ROUNDS):
        msg = reply["choices"][0]["message"]
        if not msg.get("tool_calls"):
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg["tool_calls"]})
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            ok, why = allow_tool_call(name, arguments, question)
            t0, s0 = time.perf_counter(), now()
            if ok:
                ok, text = await call_tool(session, name, arguments)
            else:
                text = f"Refused: {why}."
            timings["tools"] = timings.get("tools", 0) + (time.perf_counter() - t0) * 1000
            record.append(ToolCall(name=name, arguments=arguments, ok=ok))
            if trace:
                trace.span(f"tool:{name}", s0, input=arguments, output=text[:2000], ok=ok)
            messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": text})
        reply = await generate(messages, tools, timings, trace)
    return reply


@router.post("/assist")
async def assist(req: AssistRequest) -> AssistResponse:
    llm_host()  # 503 early if unconfigured
    trace = tracer.start("assist", {"question": req.question})
    timings: dict[str, float] = {}
    try:
        chunks: list[Chunk] = []
        if req.use_wiki:
            t0 = time.perf_counter()
            vector = await embed_query(req.question)
            timings["embed"] = (time.perf_counter() - t0) * 1000

            t0, s0 = time.perf_counter(), now()
            chunks = await retrieve(vector)
            timings["retrieve"] = (time.perf_counter() - t0) * 1000
        if trace and req.use_wiki:
            trace.span("retrieve", s0, input=req.question, output=[{"chunk": c.id, "page": c.page_id, "score": round(c.score, 4)} for c in chunks])

        messages = build_messages(req.question, chunks)
        tool_calls: list[ToolCall] = []
        if os.environ.get("MCP_URL") and ALLOWED_TOOLS:
            try:
                async with mcp_session() as session:
                    reply = await answer_with_tools(req.question, messages, session, tool_calls, timings, trace)
            except Exception as e:  # noqa: BLE001 - sorted below
                if llm_error(e):
                    raise llm_error(e) from e
                # The MCP server is down or broken: answer from the wiki alone rather than not at all.
                log.warning("MCP unavailable, answering without tools: %s", e)
                tool_calls.clear()
                reply = await generate(build_messages(req.question, chunks), None, timings, trace)
        else:
            reply = await generate(messages, None, timings, trace)
        answer = reply["choices"][0]["message"].get("content") or ""
    except httpx2.HTTPError as e:
        raise HTTPException(503, f"LLM host unavailable: {type(e).__name__}: {e}"[:300]) from e
    except psycopg.Error as e:
        raise HTTPException(503, f"vector store unavailable: {type(e).__name__}: {e}"[:300]) from e
    if trace:
        trace.end(answer)
        tracer.send(trace)
    return AssistResponse(
        answer=answer,
        sources=[Source(page_id=c.page_id, title=c.title, chunk_id=c.id, score=round(c.score, 4)) for c in chunks],
        tool_calls=tool_calls,
        model={"chat": reply.get("model", "chat"), "embed": EMBED_MODEL},
        timings_ms={k: round(v, 1) for k, v in timings.items()},
        trace_id=trace.id if trace else None,
    )
