import json
from pathlib import Path

from sec_financial_research import cli, evaluation
from sec_financial_research.ai.tools import FilingRetrievalTool, FinancialAnalyticsTool
from sec_financial_research.application.question_agent import ResearchAgent
from sec_financial_research.application.research_service import FinancialResearchService
from tests.test_question_agent import FIXTURE as COMPANYFACTS_FIXTURE
from tests.test_question_agent import _agent_sec_client


def test_load_evaluation_dataset_parses_versioned_golden_cases(tmp_path: Path):
    dataset_path = tmp_path / "golden.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "data_mode": "deterministic_fixture",
                "fixture": {"ticker": "AAPL"},
                "cases": [
                    {
                        "id": "financial-growth",
                        "ticker": "AAPL",
                        "question": "What was revenue growth?",
                        "expected": {
                            "status": "supported",
                            "route": "financial",
                            "tools": ["financial_analytics"],
                            "ratios": {
                                "revenue_growth_pct": {
                                    "value": 6.4255,
                                    "absolute_tolerance": 0.0001,
                                }
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dataset = evaluation.load_evaluation_dataset(dataset_path)

    assert dataset.version == 1
    assert dataset.data_mode == "deterministic_fixture"
    assert dataset.cases[0].case_id == "financial-growth"
    expectation = dataset.cases[0].expected_ratios["revenue_growth_pct"]
    assert expectation.value == 6.4255
    assert expectation.absolute_tolerance == 0.0001


def test_evaluation_runner_scores_financial_accuracy_tools_citations_and_grounding(
    tmp_path: Path,
):
    dataset_path = tmp_path / "golden.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "data_mode": "deterministic_fixture",
                "fixture": {"ticker": "AAPL"},
                "cases": [
                    {
                        "id": "financial-growth",
                        "ticker": "AAPL",
                        "question": "What were revenue growth and net margin?",
                        "expected": {
                            "status": "supported",
                            "route": "financial",
                            "tools": ["financial_analytics"],
                            "ratios": {
                                "revenue_growth_pct": {
                                    "value": 6.4255,
                                    "absolute_tolerance": 0.0001,
                                }
                            },
                            "citation_sources": ["companyfacts"],
                            "evidence_requirements": [
                                "calculated_financials",
                                "companyfacts_citation",
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset = evaluation.load_evaluation_dataset(dataset_path)
    client = _agent_sec_client(tmp_path / "cache")
    agent = ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(client),
    )

    report = evaluation.run_evaluation_dataset(dataset, agent)

    assert report.passed is True
    assert report.passed_cases == 1
    assert report.total_cases == 1
    checks = report.cases[0].checks
    assert checks["numeric_accuracy"].passed is True
    assert checks["sql_or_tool_outputs"].passed is True
    assert checks["citation_presence"].passed is True
    assert checks["groundedness"].passed is True


def test_evaluation_runner_checks_retrieval_relevance_and_evidence_grounding(
    tmp_path: Path,
):
    dataset_path = tmp_path / "golden.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "data_mode": "deterministic_fixture",
                "fixture": {"ticker": "AAPL"},
                "cases": [
                    {
                        "id": "filing-supply-chain-risk",
                        "ticker": "AAPL",
                        "question": "Which supply chain disruptions are described as risks?",
                        "expected": {
                            "status": "supported",
                            "route": "filing",
                            "tools": ["filing_retrieval"],
                            "citation_sources": ["filing", "filing_index"],
                            "evidence_requirements": ["filing_chunk_citation"],
                            "retrieval": {
                                "top_section": "Item 1A — Risk Factors",
                                "required_terms": ["supply", "chain", "disruptions"],
                            },
                            "grounded_terms": ["supply", "chain", "disruptions"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset = evaluation.load_evaluation_dataset(dataset_path)
    client = _agent_sec_client(tmp_path / "cache")
    agent = ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(client),
    )

    report = evaluation.run_evaluation_dataset(dataset, agent)

    checks = report.cases[0].checks
    assert report.passed is True
    assert checks["retrieval_relevance"].passed is True
    assert checks["groundedness"].passed is True
    assert "Item 1A — Risk Factors" in checks["retrieval_relevance"].detail


def test_evaluation_runner_scores_unsupported_questions_without_tool_evidence(
    tmp_path: Path,
):
    dataset_path = tmp_path / "golden.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "data_mode": "deterministic_fixture",
                "fixture": {"ticker": "AAPL"},
                "cases": [
                    {
                        "id": "unsupported-investment-advice",
                        "ticker": "AAPL",
                        "question": "Should I buy this stock?",
                        "expected": {
                            "status": "unsupported",
                            "route": "unsupported",
                            "tools": [],
                            "limitation_terms": ["investment recommendations", "outside"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset = evaluation.load_evaluation_dataset(dataset_path)
    client = _agent_sec_client(tmp_path / "cache")
    agent = ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(client),
    )

    report = evaluation.run_evaluation_dataset(dataset, agent)

    check = report.cases[0].checks["unsupported_questions"]
    assert report.passed is True
    assert check.passed is True
    assert "evidence=none" in check.detail


def test_run_evaluation_file_builds_an_isolated_deterministic_fixture_agent(
    tmp_path: Path,
):
    filing_path = tmp_path / "apple-10k.html"
    filing_path.write_text(
        """
        <html><body>
          <h2>Item 1. Business</h2>
          <p>A global supply chain supports product manufacturing.</p>
          <h2>Item 1A. Risk Factors</h2>
          <p>Component shortages and supply chain disruptions could delay production.</p>
        </body></html>
        """,
        encoding="utf-8",
    )
    dataset_path = tmp_path / "golden.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "data_mode": "deterministic_fixture",
                "fixture": {
                    "company": {
                        "ticker": "AAPL",
                        "cik": "0000320193",
                        "name": "Apple Inc.",
                    },
                    "companyfacts_path": str(COMPANYFACTS_FIXTURE),
                    "filing": {
                        "accession": "0000320193-25-000079",
                        "filing_date": "2025-10-31",
                        "report_date": "2025-09-27",
                        "form": "10-K",
                        "primary_document": "aapl-20250927.htm",
                        "html_path": str(filing_path),
                    },
                },
                "cases": [
                    {
                        "id": "financial-growth",
                        "ticker": "AAPL",
                        "question": "What was revenue growth?",
                        "expected": {
                            "status": "supported",
                            "route": "financial",
                            "tools": ["financial_analytics"],
                            "ratios": {
                                "revenue_growth_pct": {
                                    "value": 6.425511782832739,
                                    "absolute_tolerance": 1e-9,
                                }
                            },
                            "citation_sources": ["companyfacts"],
                            "evidence_requirements": [
                                "calculated_financials",
                                "companyfacts_citation",
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluation.run_evaluation_file(dataset_path)

    assert report.passed is True
    assert report.passed_cases == report.total_cases == 1


def test_cli_evaluate_runs_the_committed_golden_dataset(capsys):
    dataset_path = Path(__file__).parents[1] / "evals" / "golden_questions.json"

    exit_code = cli.main(["evaluate", "--dataset", str(dataset_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "Golden evaluation (deterministic_fixture): 6/6 cases passed" in captured.out
    assert "numeric_accuracy: 2/2 checks passed" in captured.out
    assert "sql_or_tool_outputs: 6/6 checks passed" in captured.out
    assert "citation_presence: 3/3 checks passed" in captured.out
    assert "retrieval_relevance: 2/2 checks passed" in captured.out
    assert "groundedness: 3/3 checks passed" in captured.out
    assert "unsupported_questions: 3/3 checks passed" in captured.out


def test_readme_documents_the_reproducible_golden_evaluation_results():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "uv run sec-research evaluate" in readme
    assert "deterministic_fixture" in readme
    assert "6/6 golden cases passed" in readme
    assert "numeric accuracy" in readme.lower()
    assert "unsupported questions" in readme.lower()
