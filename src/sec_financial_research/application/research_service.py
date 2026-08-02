"""Application use case for building an evidence-backed company report."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sec_financial_research.analytics.financials import build_financial_snapshot
from sec_financial_research.domain.models import (
    Citation,
    CompanyIdentity,
    ResearchReport,
)


class SECDataSource(Protocol):
    def resolve_ticker(self, ticker: str) -> CompanyIdentity: ...

    def get_company_facts(self, cik: str) -> dict: ...


class FinancialResearchService:
    def __init__(self, sec_client: SECDataSource) -> None:
        self.sec_client = sec_client

    def research_company(self, ticker: str) -> ResearchReport:
        identity = self.sec_client.resolve_ticker(ticker.strip().upper())
        company_facts = self.sec_client.get_company_facts(identity.cik)
        snapshot = build_financial_snapshot(identity, company_facts)

        growth = snapshot.ratios["revenue_growth_pct"]
        direction = "grew" if growth >= 0 else "declined"
        summary = (
            f"Revenue {direction} {abs(growth):.2f}% year over year to "
            f"{_currency(snapshot.metrics['revenue'].value)}. "
            f"Net margin was {snapshot.ratios['net_margin_pct']:.2f}% and operating "
            f"cash flow converted {snapshot.ratios['cash_conversion_pct']:.2f}% of net income."
        )

        accession = snapshot.metrics["revenue"].accession
        accession_compact = accession.replace("-", "")
        cik_unpadded = str(int(identity.cik))
        citations = (
            Citation(
                title=f"SEC Companyfacts for {identity.name}",
                url=(
                    "https://data.sec.gov/api/xbrl/companyfacts/"
                    f"CIK{identity.cik}.json"
                ),
            ),
            Citation(
                title=f"SEC filing {accession}",
                url=(
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{cik_unpadded}/{accession_compact}/{accession}-index.html"
                ),
            ),
        )
        return ResearchReport(
            snapshot=snapshot,
            summary=summary,
            citations=citations,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


def _currency(value: int | float) -> str:
    absolute = abs(float(value))
    if absolute >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if absolute >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.2f}"
