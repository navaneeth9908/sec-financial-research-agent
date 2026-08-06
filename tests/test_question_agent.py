import json
from pathlib import Path

import pytest

from sec_financial_research import cli
from sec_financial_research.ai.tools import (
    FilingRetrievalTool,
    FinancialAnalyticsTool,
)
from sec_financial_research.application.question_agent import (
    AnswerStatus,
    DeterministicQuestionPlanner,
    EvidenceRequirement,
    QuestionRoute,
    ResearchAgent,
    ResearchToolName,
)
from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.infrastructure.sec_client import SECClient

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"


def _agent_sec_client(tmp_path: Path) -> SECClient:
    company_facts = json.loads(FIXTURE.read_text(encoding="utf-8"))
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "filingDate": ["2025-10-31"],
                "reportDate": ["2025-09-27"],
                "form": ["10-K"],
                "primaryDocument": ["aapl-20250927.htm"],
            }
        },
    }

    def json_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                }
            }
        if "companyfacts" in url:
            return company_facts
        return submissions

    filing_html = """
    <html><body>
      <h2>Item 1. Business</h2>
      <p>A global supply chain supports product manufacturing.</p>
      <h2>Item 1A. Risk Factors</h2>
      <p>Component shortages and supply chain disruptions could delay production.</p>
    </body></html>
    """
    return SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=json_transport,
        text_transport=lambda url, headers, timeout: filing_html,
    )


def test_planner_combines_calculated_financials_with_cited_filing_retrieval():
    plan = DeterministicQuestionPlanner().plan(
        "How did revenue grow and what supply chain risks could affect the company?"
    )

    assert plan.route is QuestionRoute.COMBINED
    assert [call.tool for call in plan.tool_calls] == [
        ResearchToolName.FINANCIAL_ANALYTICS,
        ResearchToolName.FILING_RETRIEVAL,
    ]
    assert plan.evidence_requirements == (
        EvidenceRequirement.CALCULATED_FINANCIALS,
        EvidenceRequirement.COMPANYFACTS_CITATION,
        EvidenceRequirement.FILING_CHUNK_CITATION,
    )
    assert plan.limitation is None


def test_planner_routes_financial_question_to_calculated_tool_only():
    plan = DeterministicQuestionPlanner().plan(
        "What were revenue growth and net margin?"
    )

    assert plan.route is QuestionRoute.FINANCIAL
    assert [call.tool for call in plan.tool_calls] == [
        ResearchToolName.FINANCIAL_ANALYTICS
    ]
    assert plan.evidence_requirements == (
        EvidenceRequirement.CALCULATED_FINANCIALS,
        EvidenceRequirement.COMPANYFACTS_CITATION,
    )


def test_planner_routes_filing_question_to_citation_preserving_retrieval():
    plan = DeterministicQuestionPlanner().plan(
        "Which cybersecurity risks did the filing disclose?"
    )

    assert plan.route is QuestionRoute.FILING
    assert [call.tool for call in plan.tool_calls] == [
        ResearchToolName.FILING_RETRIEVAL
    ]
    assert plan.evidence_requirements == (
        EvidenceRequirement.FILING_CHUNK_CITATION,
    )


def test_planner_explicitly_rejects_investment_recommendations():
    plan = DeterministicQuestionPlanner().plan(
        "Given its revenue growth, should I buy this stock?"
    )

    assert plan.route is QuestionRoute.UNSUPPORTED
    assert plan.tool_calls == ()
    assert plan.evidence_requirements == ()
    assert plan.limitation is not None
    assert "investment recommendations" in plan.limitation


def test_planner_explicitly_rejects_forecasts():
    plan = DeterministicQuestionPlanner().plan(
        "Forecast next year's revenue growth."
    )

    assert plan.route is QuestionRoute.UNSUPPORTED
    assert plan.tool_calls == ()
    assert plan.evidence_requirements == ()
    assert plan.limitation is not None
    assert "forecasts" in plan.limitation


def test_planner_returns_a_limitation_for_out_of_scope_questions():
    plan = DeterministicQuestionPlanner().plan(
        "Who won the football match yesterday?"
    )

    assert plan.route is QuestionRoute.UNSUPPORTED
    assert plan.tool_calls == ()
    assert plan.evidence_requirements == ()
    assert plan.limitation == (
        "Question is outside the supported SEC financial and filing research scope."
    )


def test_agent_executes_combined_plan_with_calculated_and_filing_evidence(tmp_path: Path):
    company_facts = json.loads(FIXTURE.read_text(encoding="utf-8"))
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "filingDate": ["2025-10-31"],
                "reportDate": ["2025-09-27"],
                "form": ["10-K"],
                "primaryDocument": ["aapl-20250927.htm"],
            }
        },
    }

    def json_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                }
            }
        if "companyfacts" in url:
            return company_facts
        return submissions

    filing_html = """
    <html><body>
      <h2>Item 1. Business</h2>
      <p>A global supply chain supports product manufacturing.</p>
      <h2>Item 1A. Risk Factors</h2>
      <p>Component shortages and supply chain disruptions could delay production.</p>
    </body></html>
    """
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=json_transport,
        text_transport=lambda url, headers, timeout: filing_html,
    )
    agent = ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(
            client,
            chunk_size=200,
            overlap_chars=20,
        ),
    )

    answer = agent.answer(
        "AAPL",
        "How did revenue grow and what supply chain risks could affect the company?",
        filing_limit=2,
    )

    assert answer.status is AnswerStatus.SUPPORTED
    assert answer.plan.route is QuestionRoute.COMBINED
    assert answer.satisfied_evidence == answer.plan.evidence_requirements
    assert answer.financial is not None
    assert "revenue_growth_pct" in answer.financial.report.snapshot.ratios
    assert len(answer.financial.report.citations) == 2
    assert answer.filing is not None
    assert answer.filing.identity.ticker == "AAPL"
    assert answer.filing.matches[0].chunk.section == "Item 1A — Risk Factors"
    assert answer.filing.matches[0].chunk.accession == "0000320193-25-000079"
    assert answer.filing.matches[0].chunk.source_url.startswith(
        "https://www.sec.gov/Archives/edgar/data/320193/"
    )
    assert answer.limitation is None


def test_cli_ask_outputs_the_plan_calculations_and_filing_citations(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: _agent_sec_client(tmp_path))
    question = (
        "How did revenue grow and what supply chain risks could affect the company?"
    )

    exit_code = cli.main(["ask", "AAPL", question, "--top-k", "1"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["status"] == "supported"
    assert payload["question"] == question
    assert payload["plan"]["route"] == "combined"
    assert [call["tool"] for call in payload["plan"]["tool_calls"]] == [
        "financial_analytics",
        "filing_retrieval",
    ]
    assert payload["evidence"]["financial"]["ratios"][
        "revenue_growth_pct"
    ] == pytest.approx(6.4255, rel=1e-4)
    assert payload["evidence"]["financial"]["citations"][0]["url"].startswith(
        "https://data.sec.gov/api/xbrl/companyfacts/"
    )
    assert payload["evidence"]["filing"][0]["accession"] == (
        "0000320193-25-000079"
    )
    assert payload["evidence"]["filing"][0]["source_url"].startswith(
        "https://www.sec.gov/Archives/edgar/data/320193/"
    )
    assert payload["evidence_gate"]["satisfied"] == (
        payload["evidence_gate"]["required"]
    )


def test_agent_refuses_to_answer_when_required_filing_evidence_is_missing(
    tmp_path: Path,
):
    client = _agent_sec_client(tmp_path)
    agent = ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(client),
    )

    answer = agent.answer("AAPL", "Which cybersecurity controls were disclosed?")

    assert answer.status is AnswerStatus.UNSUPPORTED
    assert answer.satisfied_evidence == ()
    assert answer.financial is None
    assert answer.filing is None
    assert answer.limitation == "No cited filing passage matched the question."
