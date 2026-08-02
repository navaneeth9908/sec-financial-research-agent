import json
from pathlib import Path

import pytest

from sec_financial_research.analytics.financials import (
    annual_series,
    build_financial_snapshot,
)
from sec_financial_research.domain.models import CompanyIdentity

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"


def load_facts() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


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
