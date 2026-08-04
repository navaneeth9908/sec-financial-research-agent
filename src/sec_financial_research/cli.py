"""Command-line interface for live SEC financial research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sec_financial_research.application.research_service import FinancialResearchService
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = SECClient.from_env()
        service = FinancialResearchService(client)
        if args.command == "filings":
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

    if args.command == "filings":
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
