"""Command-line interface for live SEC financial research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sec_financial_research.ai.retrieval import HybridFilingRetriever
from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.domain.models import FilingChunk
from sec_financial_research.infrastructure.filing_parser import (
    chunk_filing_document,
    extract_filing_text,
)
from sec_financial_research.infrastructure.research_mart import DuckDBResearchMart
from sec_financial_research.infrastructure.sec_client import SECClient, SECClientError
from sec_financial_research.interfaces.reporting import (
    comparison_to_dict,
    render_comparison_markdown,
    render_markdown,
    report_to_dict,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sec-research",
        description="Build a cited financial research report from public SEC EDGAR data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="Generate the latest annual company report")
    report.add_argument("ticker", help="Public-company ticker, for example AAPL or MSFT")
    report.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    report.add_argument("--output", type=Path, help="Optional output file")

    compare = subparsers.add_parser(
        "compare", help="Compare latest annual metrics for two or more companies"
    )
    compare.add_argument(
        "tickers", nargs="+", help="Public-company tickers, for example AAPL NVDA"
    )
    compare.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    compare.add_argument("--output", type=Path, help="Optional output file")

    filings = subparsers.add_parser(
        "filings",
        help="Fetch recent 10-K/10-Q metadata and cache primary filing documents",
    )
    filings.add_argument(
        "ticker", help="Public-company ticker, for example AAPL or MSFT"
    )
    filings.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of matching filings to fetch (default: 5)",
    )
    filings.add_argument(
        "--form",
        action="append",
        choices=("10-K", "10-Q"),
        dest="forms",
        help="Limit to one form type; repeat to include both",
    )

    filing_chunks = subparsers.add_parser(
        "filing-chunks",
        help="Extract and sample citation-preserving chunks from the latest 10-K",
    )
    filing_chunks.add_argument(
        "ticker", help="Public-company ticker, for example AAPL or MSFT"
    )
    filing_chunks.add_argument(
        "--max-chunks",
        type=int,
        default=3,
        help="Maximum sample chunks to print (default: 3)",
    )
    filing_chunks.add_argument(
        "--chunk-size",
        type=int,
        default=1_800,
        help="Maximum characters per chunk (default: 1800)",
    )
    filing_chunks.add_argument(
        "--overlap-chars",
        type=int,
        default=200,
        help="Approximate whole-word overlap between chunks (default: 200)",
    )

    filing_search = subparsers.add_parser(
        "filing-search",
        help="Query the latest 10-K and return ranked evidence with SEC citations",
    )
    filing_search.add_argument(
        "ticker", help="Public-company ticker, for example AAPL or MSFT"
    )
    filing_search.add_argument("query", help="Question or evidence search query")
    filing_search.add_argument(
        "--top-k",
        "--limit",
        dest="top_k",
        type=int,
        default=3,
        help="Maximum ranked evidence chunks to print (default: 3)",
    )
    filing_search.add_argument(
        "--mode",
        choices=("lexical", "hybrid"),
        default="hybrid",
        help="BM25 lexical or section-aware hybrid ranking (default: hybrid)",
    )
    filing_search.add_argument(
        "--chunk-size",
        type=int,
        default=1_800,
        help="Maximum characters per chunk (default: 1800)",
    )
    filing_search.add_argument(
        "--overlap-chars",
        type=int,
        default=200,
        help="Approximate whole-word overlap between chunks (default: 200)",
    )

    mart_load = subparsers.add_parser(
        "mart-load",
        help="Ingest a company snapshot into the DuckDB analytical mart",
    )
    mart_load.add_argument(
        "ticker", help="Public-company ticker, for example AAPL or MSFT"
    )
    mart_load.add_argument(
        "--database",
        type=Path,
        default=Path(".cache/research.duckdb"),
        help="DuckDB database path (default: .cache/research.duckdb)",
    )
    return parser


def _format_cache_age(age_seconds: float) -> str:
    if age_seconds >= 3_600:
        return f"{age_seconds / 3_600:.0f}h"
    if age_seconds >= 60:
        return f"{age_seconds / 60:.0f}m"
    return f"{age_seconds:.0f}s"


def _warn_on_stale_cache(client: SECClient) -> None:
    for metadata in client.cache_metadata.values():
        if metadata.status != "stale":
            continue
        refresh_detail = metadata.refresh_error or "failed for an unknown reason"
        refresh_detail = refresh_detail.removeprefix("SEC request ")
        print(
            f"warning: stale SEC cache used for {metadata.cache_key} "
            f"({_format_cache_age(metadata.age_seconds)} old); refresh {refresh_detail}",
            file=sys.stderr,
        )


def _representative_filing_chunks(
    chunks: tuple[FilingChunk, ...], limit: int
) -> tuple[FilingChunk, ...]:
    section_order: list[str] = []
    representatives: dict[str, FilingChunk] = {}
    for chunk in chunks:
        if chunk.section == "Preamble":
            continue
        if chunk.section not in representatives:
            section_order.append(chunk.section)
            representatives[chunk.section] = chunk
        elif len(chunk.text) > len(representatives[chunk.section].text):
            representatives[chunk.section] = chunk

    selected = [representatives[section] for section in section_order[:limit]]
    selected_ids = {chunk.chunk_id for chunk in selected}
    for chunk in chunks:
        if len(selected) == limit:
            break
        if chunk.chunk_id in selected_ids:
            continue
        selected.append(chunk)
    return tuple(selected)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = SECClient.from_env()
        service = FinancialResearchService(client)
        if args.command in {"filing-chunks", "filing-search"}:
            if args.command == "filing-chunks" and args.max_chunks < 1:
                raise ValueError("max-chunks must be at least 1")
            identity = client.resolve_ticker(args.ticker)
            filings = client.get_recent_filings(
                identity.cik,
                forms=("10-K",),
                limit=1,
            )
            if not filings:
                raise SECClientError(f"No recent 10-K filing was found for {identity.ticker}")
            filing = filings[0]
            document = client.get_filing_document(filing)
            extracted_text = extract_filing_text(document.text)
            chunks = chunk_filing_document(
                document,
                max_chars=args.chunk_size,
                overlap_chars=args.overlap_chars,
            )
            cache_key = f"filing_{filing.accession}_{filing.primary_document}"
            company_payload = {
                "ticker": identity.ticker,
                "cik": identity.cik,
                "name": identity.name,
            }
            filing_payload = {
                "accession": filing.accession,
                "form": filing.form,
                "filing_date": filing.filing_date,
                "report_date": filing.report_date,
                "primary_document_url": filing.primary_document_url,
                "index_url": filing.index_url,
                "cache_status": client.cache_metadata[cache_key].status,
            }
            if args.command == "filing-search":
                evidence = HybridFilingRetriever(chunks).search(
                    args.query,
                    limit=args.top_k,
                    mode=args.mode,
                )
                result = {
                    "query": args.query,
                    "mode": args.mode,
                    "company": company_payload,
                    "filing": filing_payload,
                    "retrieval": {
                        "mode": args.mode,
                        "candidate_chunks": len(chunks),
                        "returned_evidence": len(evidence),
                        "chunk_count": len(chunks),
                        "evidence_count": len(evidence),
                    },
                    "evidence": [
                        {
                            "rank": match.rank,
                            "score": match.score,
                            "lexical_score": match.lexical_score,
                            "matched_terms": list(match.matched_terms),
                            "chunk_id": match.chunk.chunk_id,
                            "chunk_index": match.chunk.chunk_index,
                            "cik": match.chunk.cik,
                            "company_name": match.chunk.company_name,
                            "accession": match.chunk.accession,
                            "form": match.chunk.form,
                            "filing_date": match.chunk.filing_date,
                            "report_date": match.chunk.report_date,
                            "section": match.chunk.section,
                            "text": match.chunk.text,
                            "source_url": match.chunk.source_url,
                            "index_url": match.chunk.index_url,
                            "citations": {
                                "primary_document_url": match.chunk.source_url,
                                "filing_index_url": match.chunk.index_url,
                            },
                        }
                        for match in evidence
                    ],
                }
            else:
                result = {
                    "company": company_payload,
                    "filing": filing_payload,
                    "extraction": {
                        "document_characters": len(document.text),
                        "extracted_characters": len(extracted_text),
                        "chunk_count": len(chunks),
                        "sections": list(
                            dict.fromkeys(chunk.section for chunk in chunks)
                        ),
                    },
                    "sample_chunks": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "chunk_index": chunk.chunk_index,
                            "section": chunk.section,
                            "text": chunk.text,
                            "accession": chunk.accession,
                            "source_url": chunk.source_url,
                            "index_url": chunk.index_url,
                        }
                        for chunk in _representative_filing_chunks(
                            chunks, args.max_chunks
                        )
                    ],
                }
        elif args.command == "filings":
            identity = client.resolve_ticker(args.ticker)
            forms = tuple(args.forms) if args.forms else ("10-K", "10-Q")
            filings = client.get_recent_filings(
                identity.cik,
                forms=forms,
                limit=args.limit,
            )
            filing_rows = []
            for filing in filings:
                document = client.get_filing_document(filing)
                cache_key = f"filing_{filing.accession}_{filing.primary_document}"
                filing_rows.append(
                    {
                        "accession": filing.accession,
                        "form": filing.form,
                        "filing_date": filing.filing_date,
                        "report_date": filing.report_date,
                        "primary_document_url": filing.primary_document_url,
                        "index_url": filing.index_url,
                        "document_characters": len(document.text),
                        "cache_status": client.cache_metadata[cache_key].status,
                    }
                )
            result = {
                "company": {
                    "ticker": identity.ticker,
                    "cik": identity.cik,
                    "name": identity.name,
                },
                "submissions_url": (
                    filings[0].submissions_url
                    if filings
                    else "https://data.sec.gov/submissions/"
                    f"CIK{identity.cik}.json"
                ),
                "filings": filing_rows,
            }
        elif args.command == "compare":
            result = service.research_companies(args.tickers)
        else:
            result = service.research_company(args.ticker)
    except (SECClientError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _warn_on_stale_cache(client)

    if args.command in {"filings", "filing-chunks", "filing-search"}:
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "mart-load":
        source_url = result.citations[0].url
        with DuckDBResearchMart(args.database) as mart:
            mart.ingest_snapshot(result.snapshot, source_url=source_url)
            metrics = mart.company_metrics(result.snapshot.identity.ticker)
            ratios = mart.company_ratios(result.snapshot.identity.ticker)
        rendered = json.dumps(
            {
                "database": str(args.database),
                "ticker": result.snapshot.identity.ticker,
                "fiscal_end": result.snapshot.fiscal_end,
                "metric_rows": len(metrics),
                "ratio_rows": len(ratios),
                "source_url": source_url,
            },
            indent=2,
        )
        print(rendered)
        return 0

    if args.command == "compare":
        if args.output_format == "json":
            rendered = json.dumps(comparison_to_dict(result), indent=2)
        else:
            rendered = render_comparison_markdown(result)
    elif args.output_format == "json":
        rendered = json.dumps(report_to_dict(result), indent=2)
    else:
        rendered = render_markdown(result)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
