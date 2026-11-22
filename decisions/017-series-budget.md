# 017: Keep about 350 series by keeping only what a panel, an SLO or an incident queries

Stage: S5 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

`observability/alloy/budget.alloy` keeps an allow-list per source and drops everything else:
- **Host (node_exporter):** CPU, load, the memory totals, filesystem size and free, network bytes, boot time. It drops tmpfs, overlay and similar filesystems and Docker's `veth*`, `br-*`, `lo` devices.
- **Containers (cAdvisor):** CPU, memory (working set and usage), network bytes, start time. It drops the `id` and `image` labels, which change on every deploy.
- **Services:** all `lab_*` metrics (contracts/metrics.yaml) and Traefik's per-service request count and latency histogram (what S8's canary compares). It drops the client libraries' `python_*` and `process_*` families and Traefik's other families.
- `up` and `scrape_samples_scraped` everywhere: the first for "is it scraped", the second so the size of each raw scrape stays visible.

## Rejected alternative

Send everything and rely on the free tier being large enough.

## Why

Measured with `scrape_samples_scraped` (series each scrape produces before the budget): the app host about 1,888 (cAdvisor 885, node_exporter 650, Traefik 173, the two app colours 150, serving 30) and the platform host about 1,453, so about 3,341 in total. That is already a third of the 10,000 limit with only two hosts, and S6 adds the LLM host (roughly 1,500 more before the budget: one more node_exporter and one more cAdvisor) plus the assistant's own metrics. Everything unfiltered would sit around 5,000 by tonight, and any label that changes per deploy (`id`, `image`) grows it with every release. After the budget the total is 346 series (Grafana's own count of active series; Alloy's write-ahead logs agree: 284 on the app host, 61 on the platform host, 0 samples refused), about 3.5% of the limit, so S6, S8's canary labels and a burst of deploys all fit with large margin. I re-ran CD three times in a row and the count moved from 351 to 346, so a deploy doesn't mint new series. The cost: a metric I dropped isn't there when an incident needs it (per-device disk I/O, per-CPU detail, Traefik's entrypoint metrics), so the fix in an incident is to add it to the allow-list and redeploy Alloy, about 3 minutes.

## What would show this was wrong

Total active series above about 5,000 (half the limit) after S6, a panel or incident query that returns nothing because the series was dropped, or a new label with unbounded values appearing in `count by (host, job)` after a deploy.
