import json
from pathlib import Path

from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.interfaces.reporting import report_to_dict, render_markdown
from tests.test_research_service import FakeSECClient


def test_markdown_report_contains_metrics_ratios_and_sources():
    report = FinancialResearchService(FakeSECClient()).research_company("AAPL")

    rendered = render_markdown(report)

    assert "# Apple Inc. (AAPL)" in rendered
    assert "$416.16B" in rendered
    assert "6.43%" in rendered
    assert "SEC Companyfacts" in rendered
    assert "SEC filing 0000320193-25-000079" in rendered
    assert "not investment advice" in rendered.lower()


def test_report_dict_is_json_serializable():
    report = FinancialResearchService(FakeSECClient()).research_company("AAPL")

    payload = report_to_dict(report)

    assert payload["company"]["ticker"] == "AAPL"
    assert payload["metrics"]["revenue"]["value"] == 416_161_000_000
    json.dumps(payload)
