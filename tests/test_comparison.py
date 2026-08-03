import json
from pathlib import Path
from typing import ClassVar

import pytest

from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.domain.models import CompanyIdentity
from sec_financial_research.interfaces import reporting

FIXTURES = Path(__file__).parent / "fixtures"


class ComparisonSECClient:
    cache_metadata: ClassVar[dict] = {}
    identities: ClassVar[dict[str, CompanyIdentity]] = {
        "AAPL": CompanyIdentity(ticker="AAPL", cik="0000320193", name="Apple Inc."),
        "NVDA": CompanyIdentity(ticker="NVDA", cik="0001045810", name="NVIDIA CORP"),
    }
    fixture_names: ClassVar[dict[str, str]] = {
        "0000320193": "apple_companyfacts_min.json",
        "0001045810": "nvidia_companyfacts_min.json",
    }

    def resolve_ticker(self, ticker: str) -> CompanyIdentity:
        return self.identities[ticker]

    def get_company_facts(self, cik: str) -> dict:
        return json.loads(
            (FIXTURES / self.fixture_names[cik]).read_text(encoding="utf-8")
        )


class NonUSDComparisonClient(ComparisonSECClient):
    def get_company_facts(self, cik: str) -> dict:
        facts = super().get_company_facts(cik)
        if cik == "0000320193":
            units = facts["facts"]["us-gaap"][
                "RevenueFromContractWithCustomerExcludingAssessedTax"
            ]["units"]
            units["EUR"] = units.pop("USD")
        return facts


def test_service_compares_normalized_metrics_and_ranks_each_ratio():
    comparison = FinancialResearchService(ComparisonSECClient()).research_companies(
        ["AAPL", "NVDA"]
    )

    assert [entry.snapshot.identity.ticker for entry in comparison.companies] == [
        "AAPL",
        "NVDA",
    ]
    assert comparison.companies[0].normalized_metrics["revenue"] == pytest.approx(
        416.161
    )
    assert comparison.companies[1].normalized_metrics["revenue"] == pytest.approx(
        215.938
    )
    assert comparison.ratio_rankings["revenue_growth_pct"] == ("NVDA", "AAPL")
    assert comparison.ratio_rankings["net_margin_pct"] == ("NVDA", "AAPL")


@pytest.mark.parametrize(
    "tickers",
    [[], ["AAPL"], ["AAPL", " aapl "], ["AAPL", "NVDA", "aapl"]],
)
def test_service_requires_two_distinct_companies(tickers: list[str]):
    with pytest.raises(
        ValueError, match="Comparison requires at least two distinct tickers"
    ):
        FinancialResearchService(ComparisonSECClient()).research_companies(tickers)


def test_service_rejects_non_usd_metrics_instead_of_mislabeling_them():
    with pytest.raises(
        ValueError,
        match="Cannot normalize AAPL revenue from EUR; expected USD",
    ):
        FinancialResearchService(NonUSDComparisonClient()).research_companies(
            ["AAPL", "NVDA"]
        )


def test_markdown_comparison_shows_normalized_metrics_rankings_and_citations():
    comparison = FinancialResearchService(ComparisonSECClient()).research_companies(
        ["AAPL", "NVDA"]
    )

    rendered = reporting.render_comparison_markdown(comparison)

    assert "# Multi-company SEC financial comparison" in rendered
    assert "USD billions" in rendered
    assert "| Apple Inc. (AAPL) | 2025-09-27 | $416.161B |" in rendered
    assert "| NVIDIA CORP (NVDA) | 2026-01-25 | $215.938B |" in rendered
    assert "### Revenue growth" in rendered
    assert "| 1 | NVDA | 65.47% |" in rendered
    assert "Ranked highest to lowest by reported percentage" in rendered
    assert "SEC Companyfacts for Apple Inc." in rendered
    assert "SEC filing 0001045810-26-000021" in rendered


def test_comparison_dict_preserves_source_values_concepts_and_rank_values():
    comparison = FinancialResearchService(ComparisonSECClient()).research_companies(
        ["AAPL", "NVDA"]
    )

    payload = reporting.comparison_to_dict(comparison)

    apple_revenue = payload["companies"][0]["normalized_metrics"]["revenue"]
    assert apple_revenue == {
        "value": 416.161,
        "unit": "USD billions",
        "source_value": 416_161_000_000,
        "source_unit": "USD",
        "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "accession": "0000320193-25-000079",
    }
    assert payload["ratio_rankings"]["revenue_growth_pct"][0]["rank"] == 1
    assert payload["ratio_rankings"]["revenue_growth_pct"][0]["ticker"] == "NVDA"
    assert payload["ratio_rankings"]["revenue_growth_pct"][0][
        "value_pct"
    ] == pytest.approx(65.4735, rel=1e-4)
    assert len(payload["companies"][1]["citations"]) == 2
    json.dumps(payload)
