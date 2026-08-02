"""Human- and machine-readable report renderers."""

from __future__ import annotations

from sec_financial_research.domain.models import ResearchReport

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


def _currency(value: int | float) -> str:
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
