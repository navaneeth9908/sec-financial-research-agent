"""Command-line interface for live SEC financial research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.infrastructure.sec_client import SECClient, SECClientError
from sec_financial_research.interfaces.reporting import report_to_dict, render_markdown


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
        report = FinancialResearchService(client).research_company(args.ticker)
    except (SECClientError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _warn_on_stale_cache(client)

    if args.output_format == "json":
        rendered = json.dumps(report_to_dict(report), indent=2)
    else:
        rendered = render_markdown(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
