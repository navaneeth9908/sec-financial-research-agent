"""Typed, deterministic research tools for financial and filing evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sec_financial_research.ai.retrieval import FilingEvidence, HybridFilingRetriever
from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.domain.models import (
    CompanyIdentity,
    FilingDocument,
    FilingMetadata,
    ResearchReport,
)
from sec_financial_research.infrastructure.filing_parser import chunk_filing_document


class EvidenceRequirementError(ValueError):
    """Raised when a tool cannot return the evidence required for an answer."""


class FilingDataSource(Protocol):
    """SEC operations required by the filing retrieval tool."""

    def resolve_ticker(self, ticker: str) -> CompanyIdentity: ...

    def get_recent_filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...],
        limit: int,
    ) -> tuple[FilingMetadata, ...]: ...

    def get_filing_document(self, filing: FilingMetadata) -> FilingDocument: ...


@dataclass(frozen=True)
class FinancialAnalyticsResult:
    """Calculated financial output with its SEC citations intact."""

    report: ResearchReport


@dataclass(frozen=True)
class FilingRetrievalResult:
    """Ranked filing passages and the issuer/filing provenance they came from."""

    identity: CompanyIdentity
    filing: FilingMetadata
    matches: tuple[FilingEvidence, ...]


class FinancialAnalyticsTool:
    """Run deterministic SEC normalization and arithmetic through the service layer."""

    def __init__(self, service: FinancialResearchService) -> None:
        self._service = service

    def run(self, ticker: str) -> FinancialAnalyticsResult:
        report = self._service.research_company(ticker)
        if not report.citations or any(
            "sec.gov" not in citation.url for citation in report.citations
        ):
            raise EvidenceRequirementError(
                "Financial analytics did not return complete SEC citations."
            )
        if any(not point.accession for point in report.snapshot.metrics.values()):
            raise EvidenceRequirementError(
                "Financial analytics returned a metric without a filing accession."
            )
        return FinancialAnalyticsResult(report=report)


class FilingRetrievalTool:
    """Retrieve ranked passages from the latest 10-K with citation metadata."""

    def __init__(
        self,
        sec_client: FilingDataSource,
        *,
        chunk_size: int = 1_800,
        overlap_chars: int = 200,
    ) -> None:
        self._sec_client = sec_client
        self._chunk_size = chunk_size
        self._overlap_chars = overlap_chars

    def run(
        self,
        ticker: str,
        question: str,
        *,
        limit: int = 3,
    ) -> FilingRetrievalResult:
        identity = self._sec_client.resolve_ticker(ticker.strip().upper())
        filings = self._sec_client.get_recent_filings(
            identity.cik,
            forms=("10-K",),
            limit=1,
        )
        if not filings:
            raise EvidenceRequirementError(
                f"No recent 10-K evidence was found for {identity.ticker}."
            )
        filing = filings[0]
        document = self._sec_client.get_filing_document(filing)
        chunks = chunk_filing_document(
            document,
            max_chars=self._chunk_size,
            overlap_chars=self._overlap_chars,
        )
        matches = HybridFilingRetriever(chunks).search(
            question,
            limit=limit,
            mode="hybrid",
        )
        if not matches:
            raise EvidenceRequirementError(
                "No cited filing passage matched the question."
            )
        if any(
            not match.chunk.accession
            or not match.chunk.source_url.startswith(
                "https://www.sec.gov/Archives/edgar/data/"
            )
            or not match.chunk.index_url.startswith(
                "https://www.sec.gov/Archives/edgar/data/"
            )
            for match in matches
        ):
            raise EvidenceRequirementError(
                "Filing retrieval returned evidence without complete SEC citations."
            )
        return FilingRetrievalResult(
            identity=identity,
            filing=filing,
            matches=matches,
        )
