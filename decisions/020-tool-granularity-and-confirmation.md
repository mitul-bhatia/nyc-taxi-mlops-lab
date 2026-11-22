# 020: Three read-only tools, offered to the model without a human confirmation; a writing tool would always need one

Stage: S7 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The assistant may call exactly the contract's three tools: `get_model_info`, `predict_duration` and `query_zone_stats` (`ALLOWED_TOOLS` in `assistant.py`; the model is only shown those). None needs a person to confirm, because none changes anything: each one reads (serving's champion, or the `marts` table as a read-only database user). Each call still goes through `allow_tool_call` (known tool, text arguments that look like a zone name) and appears in the response's `tool_calls` and the Langfuse trace. If a tool could write (issue a credit, change a fare, reload a model), I would require a person to confirm every call, outside the model's reach (the app shows the proposed call and a staff member approves it), never the model's own "are you sure".

## Rejected alternative

One general tool (a SQL or "ask the warehouse" tool), or a confirmation step before every call.

## Why

Three narrow tools mean the worst a manipulated call can do is ask a question we already allow: a zone name, a metric from a fixed list, an hour 0 to 23, and the SQL for each is a constant. A general SQL tool would turn every prompt injection into a database question. A confirmation on a read costs the rider about a minute on a CPU LLM host that already takes 20 to 45 s per answer, for no protection: the data is also visible to the rider through the app. The protection that matters for reads is the narrow tool and the least-privilege user (decision 021), not a click.

## What would show this was wrong

A tool that grows a write (then confirmation becomes mandatory), a tool result that leaks something a rider shouldn't see (then restrict by caller, not by tool), or riders being refused useful answers by `allow_tool_call` (then loosen the check with a test).
