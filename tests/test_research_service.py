import json
from pathlib import Path

from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.domain.models import CompanyIdentity

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"


class FakeSECClient:
    def resolve_ticker(self, ticker: str) -> CompanyIdentity:
        assert ticker == "AAPL"
        return CompanyIdentity(ticker="AAPL", cik="0000320193", name="Apple Inc.")

    def get_company_facts(self, cik: str) -> dict:
        assert cik == "0000320193"
        return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_service_builds_cited_research_report():
    report = FinancialResearchService(FakeSECClient()).research_company("AAPL")

    assert report.snapshot.identity.name == "Apple Inc."
    assert "revenue grew" in report.summary.lower()
    assert len(report.citations) == 2
    assert report.citations[0].url.startswith("https://data.sec.gov/api/xbrl/companyfacts/")
    assert "000032019325000079" in report.citations[1].url
