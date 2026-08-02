# One-Week Production Roadmap

Goal: complete a recruiter-ready SEC Financial Research Agent in one week through fourteen coherent, verified build sessions.

## Day 1 — Reliable public-data foundation

- Scaffold clean architecture and deterministic tests.
- Implement SEC ticker/Companyfacts client with timeout, retry, throttle, and disk cache.
- Build a cited CLI company snapshot from live SEC data.

## Day 2 — Financial analytics engine

- Expand XBRL concept resolution and comparable annual-period handling.
- Add multi-company comparison and additional ratios/trends.

## Day 3 — Filing ingestion and retrieval

- Fetch recent 10-K/10-Q filing metadata and text.
- Build chunking, semantic retrieval, and citation-preserving filing search.

## Day 4 — Agent orchestration and evaluation

- Add typed research tools and a question router/planner.
- Add golden questions, retrieval/grounding checks, and deterministic financial-answer evals.

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
