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
5. Filing ingestion reads the issuer's SEC submissions feed, validates CIK/accession/document-path provenance, and caches selected 10-K/10-Q primary documents.
6. The filing parser removes hidden inline-XBRL/non-content markup, detects canonical 10-K Item headings, and creates bounded overlapping chunks without crossing section boundaries.
7. Each filing chunk retains deterministic ID/index fields plus CIK, accession, form, filing/report dates, section, primary-document URL, and filing-index URL for downstream citation guards.
8. The normalization layer merges ordered XBRL aliases by fiscal period and deduplicates repeated comparative 10-K facts.
9. Snapshot construction requires exact fiscal-end alignment for every metric and a consecutive annual revenue period for growth.
10. The analytics layer calculates ratios from source values.
11. The DuckDB adapter quality-checks and atomically upserts normalized facts and ratios with SEC lineage.
12. The renderer emits Markdown/JSON with filing and endpoint citations.

## Reliability rules

- Timeout every HTTP request.
- Retry only transient failures (`429`, `500`, `502`, `503`, `504`) with bounded backoff.
- Respect SEC fair-access guidance and throttle requests.
- Never hide stale-cache use: expired payloads are served only after a failed refresh, and the CLI warns with cache age and refresh diagnostics.
- Record `network`, `fresh`, or `stale` metadata for each SEC cache key so later API responses can expose the same provenance.
- Apply the same timeout, throttle, retry, stale-cache, and atomic-write controls to SEC submissions JSON and primary filing HTML.
- Reject filing metadata whose submission CIK, accession prefix, or primary-document path disagrees with the requested issuer before fetching archive content.
- Make mart ingestion idempotent by replacing one CIK/fiscal-period slice in a transaction, with primary keys as a duplicate guard.
- Reject source URLs, fiscal periods, and filing accessions that do not agree with the snapshot identity before writing any rows.
- Keep raw responses out of Git because they can be large and change over time.
- Unit tests use a compact SEC-derived fixture; live SEC checks remain explicit smoke tests.

## AI safety and grounding

- The LLM never performs authoritative arithmetic; tools return calculated values.
- Filing answers must cite retrieved chunks and accession numbers.
- Unsupported questions return an explicit limitation instead of invented facts.
- Golden evaluations check numeric accuracy, citation presence, and retrieval grounding.
