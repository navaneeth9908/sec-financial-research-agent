import json
from dataclasses import replace
from pathlib import Path

import pytest

from sec_financial_research.analytics.financials import build_financial_snapshot
from sec_financial_research.domain.models import CompanyIdentity
from sec_financial_research.infrastructure.research_mart import (
    DataQualityError,
    DuckDBResearchMart,
)

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"
SOURCE_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


def apple_snapshot():
    facts = json.loads(FIXTURE.read_text(encoding="utf-8"))
    identity = CompanyIdentity(ticker="AAPL", cik="0000320193", name="Apple Inc.")
    return build_financial_snapshot(identity, facts)


def test_ingestion_is_idempotent_and_preserves_fact_and_ratio_lineage(tmp_path: Path):
    with DuckDBResearchMart(tmp_path / "research.duckdb") as mart:
        mart.ingest_snapshot(
            apple_snapshot(),
            source_url=SOURCE_URL,
            ingested_at="2026-08-03T10:15:00+00:00",
        )
        mart.ingest_snapshot(
            apple_snapshot(),
            source_url=SOURCE_URL,
            ingested_at="2026-08-03T10:15:00+00:00",
        )

        metrics = mart.company_metrics("aapl")
        ratios = mart.company_ratios("AAPL")

    assert len(metrics) == 5
    revenue = next(row for row in metrics if row["metric_name"] == "revenue")
    assert revenue["metric_value"] == 416_161_000_000
    assert revenue["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert revenue["accession"] == "0000320193-25-000079"
    assert revenue["source_url"] == SOURCE_URL
    assert revenue["ingested_at"].isoformat() == "2026-08-03T10:15:00"

    assert len(ratios) == 4
    net_margin = next(row for row in ratios if row["ratio_name"] == "net_margin_pct")
    assert net_margin["calculation_version"] == "financial_snapshot_v1"
    assert net_margin["source_accessions"] == ["0000320193-25-000079"]
    assert net_margin["source_url"] == SOURCE_URL


def test_ingestion_rejects_a_source_url_that_does_not_match_the_company_cik(
    tmp_path: Path,
):
    with DuckDBResearchMart(tmp_path / "research.duckdb") as mart:
        with pytest.raises(
            DataQualityError,
            match="source URL must match official SEC Companyfacts endpoint",
        ):
            mart.ingest_snapshot(
                apple_snapshot(),
                source_url="https://example.com/companyfacts.json",
            )

        assert mart.company_metrics("AAPL") == []


def test_ingestion_rejects_metrics_from_a_different_fiscal_period(tmp_path: Path):
    snapshot = apple_snapshot()
    misaligned_metrics = dict(snapshot.metrics)
    misaligned_metrics["assets"] = replace(
        misaligned_metrics["assets"], end="2024-09-28"
    )
    snapshot = replace(snapshot, metrics=misaligned_metrics)

    with DuckDBResearchMart(tmp_path / "research.duckdb") as mart:
        with pytest.raises(
            DataQualityError,
            match="assets period 2024-09-28 does not match snapshot period 2025-09-27",
        ):
            mart.ingest_snapshot(snapshot, source_url=SOURCE_URL)

        assert mart.company_metrics("AAPL") == []


def test_ingestion_rejects_an_accession_from_a_different_company(tmp_path: Path):
    snapshot = apple_snapshot()
    invalid_metrics = dict(snapshot.metrics)
    invalid_metrics["revenue"] = replace(
        invalid_metrics["revenue"], accession="0001045810-25-000001"
    )
    snapshot = replace(snapshot, metrics=invalid_metrics)

    with DuckDBResearchMart(tmp_path / "research.duckdb") as mart:
        with pytest.raises(
            DataQualityError,
            match="revenue accession does not belong to CIK 0000320193",
        ):
            mart.ingest_snapshot(snapshot, source_url=SOURCE_URL)

        assert mart.company_metrics("AAPL") == []
