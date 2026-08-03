"""Human- and machine-readable report renderers."""

from __future__ import annotations

from sec_financial_research.domain.models import ComparisonReport, ResearchReport

METRIC_NAMES = {
    "revenue": "Revenue",
    "net_income": "Net income",
    "assets": "Assets",
    "liabilities": "Liabilities",
    "operating_cash_flow": "Operating cash flow",
}
RATIO_NAMES = {
    "revenue_growth_pct": "Revenue growth",
    "net_margin_pct": "Net margin",
    "liabilities_to_assets_pct": "Liabilities / assets",
    "cash_conversion_pct": "Operating cash flow / net income",
}


def _currency(value: float) -> str:
    absolute = abs(float(value))
    if absolute >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if absolute >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.2f}"


def render_markdown(report: ResearchReport) -> str:
    snapshot = report.snapshot
    lines = [
        f"# {snapshot.identity.name} ({snapshot.identity.ticker})",
        "",
        f"**Fiscal year:** {snapshot.fiscal_year}  ",
        f"**Period ended:** {snapshot.fiscal_end}",
        "",
        "## Research summary",
        "",
        report.summary,
        "",
        "## Financial snapshot",
        "",
        "| Metric | Value | SEC concept |",
        "|---|---:|---|",
    ]
    for key, point in snapshot.metrics.items():
        lines.append(f"| {METRIC_NAMES[key]} | {_currency(point.value)} | `{point.concept}` |")

    lines.extend(
        [
            "",
            "## Calculated indicators",
            "",
            "| Indicator | Value |",
            "|---|---:|",
        ]
    )
    for key, value in snapshot.ratios.items():
        lines.append(f"| {RATIO_NAMES[key]} | {value:.2f}% |")

    lines.extend(["", "## Sources", ""])
    for citation in report.citations:
        lines.append(f"- [{citation.title}]({citation.url})")
    lines.extend(
        [
            "",
            f"Generated: {report.generated_at}",
            "",
            "> Educational engineering demo only; this is not investment advice.",
        ]
    )
    return "\n".join(lines)


def render_comparison_markdown(report: ComparisonReport) -> str:
    lines = [
        "# Multi-company SEC financial comparison",
        "",
        (
            "Monetary metrics are normalized to USD billions from reported USD values. "
            "Fiscal period ends remain visible because issuer calendars may differ."
        ),
        "",
        "## Normalized financial metrics",
        "",
        "| Company | Period ended | Revenue | Net income | Assets | Liabilities | Operating cash flow |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for company in report.companies:
        identity = company.snapshot.identity
        values = company.normalized_metrics
        lines.append(
            f"| {identity.name} ({identity.ticker}) | {company.snapshot.fiscal_end} | "
            f"${values['revenue']:.3f}B | ${values['net_income']:.3f}B | "
            f"${values['assets']:.3f}B | ${values['liabilities']:.3f}B | "
            f"${values['operating_cash_flow']:.3f}B |"
        )

    lines.extend(
        [
            "",
            "## Ratio rankings",
            "",
            (
                "Ranked highest to lowest by reported percentage; rank is descriptive, "
                "not an investment rating."
            ),
        ]
    )
    companies_by_ticker = {
        company.snapshot.identity.ticker: company for company in report.companies
    }
    for ratio_name, tickers in report.ratio_rankings.items():
        lines.extend(
            [
                "",
                f"### {RATIO_NAMES[ratio_name]}",
                "",
                "| Rank | Company | Value |",
                "|---:|---|---:|",
            ]
        )
        for rank, ticker in enumerate(tickers, start=1):
            value = companies_by_ticker[ticker].snapshot.ratios[ratio_name]
            lines.append(f"| {rank} | {ticker} | {value:.2f}% |")

    lines.extend(["", "## Sources", ""])
    for company in report.companies:
        lines.append(f"### {company.snapshot.identity.ticker}")
        for citation in company.citations:
            lines.append(f"- [{citation.title}]({citation.url})")
        lines.append("")
    lines.extend(
        [
            f"Generated: {report.generated_at}",
            "",
            "> Educational engineering demo only; this is not investment advice.",
        ]
    )
    return "\n".join(lines)


def comparison_to_dict(report: ComparisonReport) -> dict:
    companies_by_ticker = {
        company.snapshot.identity.ticker: company for company in report.companies
    }
    return {
        "normalization": {
            "monetary_unit": "USD billions",
            "source_scale_divisor": 1_000_000_000,
        },
        "companies": [
            {
                "company": {
                    "ticker": company.snapshot.identity.ticker,
                    "cik": company.snapshot.identity.cik,
                    "name": company.snapshot.identity.name,
                },
                "fiscal_year": company.snapshot.fiscal_year,
                "fiscal_end": company.snapshot.fiscal_end,
                "normalized_metrics": {
                    key: {
                        "value": company.normalized_metrics[key],
                        "unit": "USD billions",
                        "source_value": point.value,
                        "source_unit": point.unit,
                        "concept": point.concept,
                        "accession": point.accession,
                    }
                    for key, point in company.snapshot.metrics.items()
                },
                "ratios": company.snapshot.ratios,
                "citations": [
                    {"title": citation.title, "url": citation.url}
                    for citation in company.citations
                ],
            }
            for company in report.companies
        ],
        "ratio_rankings": {
            ratio_name: [
                {
                    "rank": rank,
                    "ticker": ticker,
                    "value_pct": companies_by_ticker[ticker].snapshot.ratios[ratio_name],
                }
                for rank, ticker in enumerate(tickers, start=1)
            ]
            for ratio_name, tickers in report.ratio_rankings.items()
        },
        "ranking_method": (
            "Highest to lowest reported percentage; descriptive only, not an "
            "investment rating."
        ),
        "generated_at": report.generated_at,
        "disclaimer": "Educational engineering demo only; not investment advice.",
    }


def report_to_dict(report: ResearchReport) -> dict:
    snapshot = report.snapshot
    return {
        "company": {
            "ticker": snapshot.identity.ticker,
            "cik": snapshot.identity.cik,
            "name": snapshot.identity.name,
        },
        "fiscal_year": snapshot.fiscal_year,
        "fiscal_end": snapshot.fiscal_end,
        "summary": report.summary,
        "metrics": {
            key: {
                "value": point.value,
                "unit": point.unit,
                "concept": point.concept,
                "accession": point.accession,
                "filed": point.filed,
            }
            for key, point in snapshot.metrics.items()
        },
        "ratios": snapshot.ratios,
        "citations": [
            {"title": citation.title, "url": citation.url}
            for citation in report.citations
        ],
        "generated_at": report.generated_at,
        "disclaimer": "Educational engineering demo only; not investment advice.",
    }
