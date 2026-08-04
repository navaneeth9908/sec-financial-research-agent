"""Domain models shared across application and infrastructure layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompanyIdentity:
    ticker: str
    cik: str
    name: str


@dataclass(frozen=True)
class FilingMetadata:
    cik: str
    company_name: str
    accession: str
    form: str
    filing_date: str
    report_date: str
    primary_document: str
    submissions_url: str
    primary_document_url: str
    index_url: str


@dataclass(frozen=True)
class FilingDocument:
    filing: FilingMetadata
    text: str
    source_url: str


@dataclass(frozen=True)
class FactPoint:
    concept: str
    label: str
    value: int | float
    unit: str
    end: str
    filed: str
    accession: str
    form: str
    fiscal_year: int | None
    start: str | None = None


@dataclass(frozen=True)
class FinancialSnapshot:
    identity: CompanyIdentity
    fiscal_year: int
    fiscal_end: str
    metrics: dict[str, FactPoint]
    ratios: dict[str, float]


@dataclass(frozen=True)
class Citation:
    title: str
    url: str


@dataclass(frozen=True)
class ResearchReport:
    snapshot: FinancialSnapshot
    summary: str
    citations: tuple[Citation, ...]
    generated_at: str


@dataclass(frozen=True)
class CompanyComparison:
    snapshot: FinancialSnapshot
    normalized_metrics: dict[str, float]
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class ComparisonReport:
    companies: tuple[CompanyComparison, ...]
    ratio_rankings: dict[str, tuple[str, ...]]
    generated_at: str


JsonObject = dict[str, Any]
