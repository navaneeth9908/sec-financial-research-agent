# One-Week Production Roadmap

Goal: complete a recruiter-ready SEC Financial Research Agent in one week through fourteen coherent, verified build sessions.

## Day 1 — Reliable public-data foundation

- Scaffold clean architecture and deterministic tests.
- Implement SEC ticker/Companyfacts client with timeout, retry, throttle, and disk cache.
- Build a cited CLI company snapshot from live SEC data.

**Status:** complete — the client now records cache provenance, retries transient failures, serves stale data only after refresh exhaustion, and emits actionable CLI warnings/errors.

## Day 2 — Financial analytics engine

- Expand XBRL concept resolution and comparable annual-period handling.
- Add multi-company comparison and additional ratios/trends.

**Status:** in progress — ordered concept aliases now fill fiscal periods across issuer taxonomy transitions, exact-period metric alignment prevents mixed-period snapshots, and annual growth rejects nonconsecutive periods. The CLI now compares two or more issuers with source-preserving USD-billion normalization, deterministic ratio rankings, visible fiscal ends, and per-company SEC citations. A lightweight DuckDB research mart adds idempotent company-period ingestion, SEC lineage fields, pre-write data-quality gates, and a CLI load/query smoke path. Additional trend depth remains next.

## Day 3 — Filing ingestion and retrieval

- Fetch recent 10-K/10-Q filing metadata and text.
- Build chunking, semantic retrieval, and citation-preserving filing search.

**Status:** in progress — the SEC client now ingests recent 10-K/10-Q submissions metadata and primary filing documents with fair-access headers, bounded retries/throttling, atomic disk caching, cache provenance, issuer/accession/path validation, and live CLI smoke paths. Inline-XBRL HTML extraction removes hidden/non-content markup, detects canonical 10-K Item sections, and emits bounded overlapping chunks that retain accession, section, filing dates, primary-document URL, and filing-index citation metadata. Deterministic BM25 lexical retrieval and a section-aware hybrid mode now rank cited chunk evidence through the `filing-search` query demo with stable tie-breaking and transparent matched terms. Embedding-backed semantic expansion remains future work.

## Day 4 — Agent orchestration and evaluation

- Add typed research tools and a question router/planner.
- Add golden questions, retrieval/grounding checks, and deterministic financial-answer evals.

**Status:** complete — typed financial and filing tools now run through a deterministic question planner with explicit evidence gates and unsupported-question handling. A versioned six-case deterministic fixture suite measures numeric accuracy, tool contracts, SEC citation presence, retrieval relevance, groundedness, and unsupported behavior; the documented CLI run passes all six cases without an LLM judge or fabricated scores.

## Day 5 — Production API

- Add FastAPI endpoints for company reports, comparisons, and filing questions.
- Add schemas, structured errors, health/readiness checks, and API tests.

## Day 6 — Demo and observability

- Add a polished local web demo with sample companies and cited answers.
- Add structured logs, request IDs, latency/cache metrics, and screenshots/demo artifacts.

## Day 7 — Deployment and portfolio handoff

- Add Docker, GitHub Actions, security/config documentation, and final architecture evidence.
- Run full tests, live-data smoke checks, completion checklist, and recruiter-facing README polish.

## Quality gates for every session

- Follow RED → GREEN → REFACTOR for behavior changes.
- Keep SEC data provenance visible.
- Run focused tests and the full suite.
- Run a CLI/API/demo smoke path.
- Commit one coherent unit with a concise human-style message.
- Push only after verification and confirm GitHub author attribution.
