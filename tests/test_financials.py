import json
from pathlib import Path

import pytest

from sec_financial_research.analytics.financials import (
    FinancialDataError,
    annual_series,
    build_financial_snapshot,
)
from sec_financial_research.domain.models import CompanyIdentity

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"
NVIDIA_FIXTURE = Path(__file__).parent / "fixtures" / "nvidia_companyfacts_min.json"


def load_facts() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_nvidia_facts() -> dict:
    return json.loads(NVIDIA_FIXTURE.read_text(encoding="utf-8"))


def test_annual_series_deduplicates_comparative_10k_periods():
    series = annual_series(
        load_facts(),
        ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    )

    assert [point.end for point in series] == ["2024-09-28", "2025-09-27"]
    assert series[0].filed == "2025-10-31"
    assert series[-1].value == 416_161_000_000


def test_build_snapshot_calculates_research_ratios():
    identity = CompanyIdentity(ticker="AAPL", cik="0000320193", name="Apple Inc.")

    snapshot = build_financial_snapshot(identity, load_facts())

    assert snapshot.fiscal_year == 2025
    assert snapshot.fiscal_end == "2025-09-27"
    assert snapshot.metrics["revenue"].value == 416_161_000_000
    assert snapshot.metrics["net_income"].value == 112_010_000_000
    assert snapshot.ratios["revenue_growth_pct"] == pytest.approx(6.4255, rel=1e-4)
    assert snapshot.ratios["net_margin_pct"] == pytest.approx(26.9151, rel=1e-4)
    assert snapshot.ratios["liabilities_to_assets_pct"] == pytest.approx(79.4753, rel=1e-4)
    assert snapshot.ratios["cash_conversion_pct"] == pytest.approx(99.5286, rel=1e-4)


def test_build_snapshot_fills_latest_periods_from_fallback_revenue_alias():
    identity = CompanyIdentity(ticker="NVDA", cik="0001045810", name="NVIDIA CORP")

    snapshot = build_financial_snapshot(identity, load_nvidia_facts())

    assert snapshot.fiscal_year == 2026
    assert snapshot.fiscal_end == "2026-01-25"
    assert snapshot.metrics["revenue"].concept == "Revenues"
    assert {point.end for point in snapshot.metrics.values()} == {"2026-01-25"}
    assert snapshot.ratios["revenue_growth_pct"] == pytest.approx(65.4735, rel=1e-4)


def test_build_snapshot_rejects_a_metric_from_a_prior_period():
    facts = load_nvidia_facts()
    asset_points = facts["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    facts["facts"]["us-gaap"]["Assets"]["units"]["USD"] = [
        point for point in asset_points if point["end"] != "2026-01-25"
    ]
    identity = CompanyIdentity(ticker="NVDA", cik="0001045810", name="NVIDIA CORP")

    with pytest.raises(
        FinancialDataError,
        match="No assets fact matches fiscal period 2026-01-25",
    ):
        build_financial_snapshot(identity, facts)


def test_build_snapshot_rejects_nonconsecutive_revenue_periods():
    facts = load_nvidia_facts()
    revenue_points = facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
        point for point in revenue_points if point["end"] != "2025-01-26"
    ]
    identity = CompanyIdentity(ticker="NVDA", cik="0001045810", name="NVIDIA CORP")

    with pytest.raises(
        FinancialDataError,
        match="No comparable prior annual revenue period for 2026-01-25",
    ):
        build_financial_snapshot(identity, facts)
