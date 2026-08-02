# Architecture

## Component boundaries

```text
interfaces/
  CLI, FastAPI, web demo
application/
  research orchestration, question routing, use cases
domain/
  company identity, normalized facts, snapshots, reports, citations
infrastructure/
  SEC HTTP client, cache, filing parser, vector store, DuckDB repositories
analytics/
  ratios, trends, peer comparison, anomaly rules
ai/
  tool registry, retrieval, synthesis, grounding/evaluation
observability/
  structured events, traces, latency/cache/eval metrics
```

The initial vertical slice uses the same boundaries without prematurely adding heavy dependencies.

## Data flow

1. The interface asks for a ticker.
2. The application service resolves it through the SEC ticker registry.
3. The SEC adapter retrieves Companyfacts using a compliant User-Agent.
4. Raw responses are cached atomically with a TTL.
5. The normalization layer deduplicates comparative 10-K facts by fiscal period.
6. The analytics layer calculates ratios from source values.
7. The renderer emits Markdown/JSON with filing and endpoint citations.

## Reliability rules

- Timeout every HTTP request.
- Retry only transient failures (`429`, `500`, `502`, `503`, `504`) with bounded backoff.
- Respect SEC fair-access guidance and throttle requests.
- Never hide stale-cache use; future API responses will expose cache metadata.
- Keep raw responses out of Git because they can be large and change over time.
- Unit tests use a compact SEC-derived fixture; live SEC checks remain explicit smoke tests.

## AI safety and grounding

- The LLM never performs authoritative arithmetic; tools return calculated values.
- Filing answers must cite retrieved chunks and accession numbers.
- Unsupported questions return an explicit limitation instead of invented facts.
- Golden evaluations check numeric accuracy, citation presence, and retrieval grounding.
