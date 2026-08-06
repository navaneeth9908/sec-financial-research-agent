"""Deterministic golden-dataset evaluation for the research agent."""

from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sec_financial_research.ai.tools import FilingRetrievalTool, FinancialAnalyticsTool
from sec_financial_research.application.question_agent import (
    ResearchAgent,
    ResearchAnswer,
)
from sec_financial_research.application.research_service import FinancialResearchService
from sec_financial_research.infrastructure.sec_client import SECClient


@dataclass(frozen=True)
class NumericExpectation:
    """Expected numeric value with an explicit deterministic tolerance."""

    value: float
    absolute_tolerance: float


@dataclass(frozen=True)
class EvaluationCase:
    """One reviewable golden research question and its expected behavior."""

    case_id: str
    ticker: str
    question: str
    expected_status: str
    expected_route: str
    expected_tools: tuple[str, ...]
    expected_ratios: dict[str, NumericExpectation]
    expected_citation_sources: tuple[str, ...]
    expected_evidence_requirements: tuple[str, ...]
    expected_top_section: str | None
    expected_retrieval_terms: tuple[str, ...]
    expected_grounded_terms: tuple[str, ...]
    expected_limitation_terms: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationDataset:
    """Versioned evaluation cases and the fixture configuration they use."""

    version: int
    data_mode: str
    fixture: dict[str, Any]
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True)
class EvaluationCheck:
    """One deterministic quality check with reviewable evidence."""

    passed: bool
    detail: str


@dataclass(frozen=True)
class EvaluationCaseResult:
    """Scored checks for one golden question."""

    case_id: str
    passed: bool
    checks: dict[str, EvaluationCheck]


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate result calculated from actual golden-case checks."""

    dataset_version: int
    data_mode: str
    cases: tuple[EvaluationCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def total_cases(self) -> int:
        return len(self.cases)

    @property
    def category_totals(self) -> dict[str, tuple[int, int]]:
        category_checks: dict[str, list[bool]] = {}
        for case in self.cases:
            for category, check in case.checks.items():
                category_checks.setdefault(category, []).append(check.passed)
        return {
            category: (sum(results), len(results))
            for category, results in sorted(category_checks.items())
        }


def load_evaluation_dataset(path: Path | str) -> EvaluationDataset:
    """Load typed golden cases from a versioned JSON dataset."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = tuple(_load_case(case) for case in payload["cases"])
    return EvaluationDataset(
        version=int(payload["version"]),
        data_mode=str(payload["data_mode"]),
        fixture=dict(payload["fixture"]),
        cases=cases,
    )


def run_evaluation_dataset(
    dataset: EvaluationDataset,
    agent: ResearchAgent,
) -> EvaluationReport:
    """Execute golden questions through the real deterministic agent path."""

    results = tuple(_evaluate_case(case, agent) for case in dataset.cases)
    return EvaluationReport(
        dataset_version=dataset.version,
        data_mode=dataset.data_mode,
        cases=results,
    )


def format_evaluation_report(report: EvaluationReport) -> str:
    """Render compact case and category counts without inventing a score."""

    lines = [
        (
            f"Golden evaluation ({report.data_mode}): "
            f"{report.passed_cases}/{report.total_cases} cases passed"
        )
    ]
    for case in report.cases:
        lines.append(f"{'PASS' if case.passed else 'FAIL'} {case.case_id}")
        if not case.passed:
            for category, check in case.checks.items():
                if not check.passed:
                    lines.append(f"  {category}: {check.detail}")
    lines.append("Category checks:")
    for category, (passed, total) in report.category_totals.items():
        lines.append(f"  {category}: {passed}/{total} checks passed")
    return "\n".join(lines)


def run_evaluation_file(path: Path | str) -> EvaluationReport:
    """Load and run a dataset using only its declared deterministic fixtures."""

    dataset_path = Path(path).resolve()
    dataset = load_evaluation_dataset(dataset_path)
    if dataset.data_mode != "deterministic_fixture":
        raise ValueError(
            "run_evaluation_file requires data_mode='deterministic_fixture'"
        )
    with tempfile.TemporaryDirectory(prefix="sec-research-eval-") as cache_dir:
        agent = _build_fixture_agent(
            dataset,
            dataset_path=dataset_path,
            cache_dir=Path(cache_dir),
        )
        return run_evaluation_dataset(dataset, agent)


def _build_fixture_agent(
    dataset: EvaluationDataset,
    *,
    dataset_path: Path,
    cache_dir: Path,
) -> ResearchAgent:
    fixture = dataset.fixture
    company = fixture["company"]
    filing = fixture["filing"]
    companyfacts_path = _resolve_fixture_path(
        dataset_path,
        str(fixture["companyfacts_path"]),
    )
    filing_html_path = _resolve_fixture_path(
        dataset_path,
        str(filing["html_path"]),
    )
    companyfacts = json.loads(companyfacts_path.read_text(encoding="utf-8"))
    filing_html = filing_html_path.read_text(encoding="utf-8")
    submissions = {
        "cik": str(company["cik"]),
        "name": str(company["name"]),
        "filings": {
            "recent": {
                "accessionNumber": [str(filing["accession"])],
                "filingDate": [str(filing["filing_date"])],
                "reportDate": [str(filing["report_date"])],
                "form": [str(filing["form"])],
                "primaryDocument": [str(filing["primary_document"])],
            }
        },
    }

    def json_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": int(str(company["cik"])),
                    "ticker": str(company["ticker"]),
                    "title": str(company["name"]),
                }
            }
        if "/companyfacts/" in url:
            return companyfacts
        return submissions

    client = SECClient(
        user_agent="sec-research-evaluation eval@example.com",
        cache_dir=cache_dir,
        throttle_seconds=0,
        transport=json_transport,
        text_transport=lambda url, headers, timeout: filing_html,
    )
    return ResearchAgent(
        financial_tool=FinancialAnalyticsTool(FinancialResearchService(client)),
        filing_tool=FilingRetrievalTool(
            client,
            chunk_size=int(fixture.get("chunk_size", 1_800)),
            overlap_chars=int(fixture.get("overlap_chars", 200)),
        ),
    )


def _resolve_fixture_path(dataset_path: Path, configured_path: str) -> Path:
    fixture_path = Path(configured_path)
    if not fixture_path.is_absolute():
        fixture_path = dataset_path.parent / fixture_path
    return fixture_path.resolve()


def _load_case(payload: dict[str, Any]) -> EvaluationCase:
    expected = payload["expected"]
    ratios = {
        name: NumericExpectation(
            value=float(config["value"]),
            absolute_tolerance=float(config["absolute_tolerance"]),
        )
        for name, config in expected.get("ratios", {}).items()
    }
    return EvaluationCase(
        case_id=str(payload["id"]),
        ticker=str(payload["ticker"]),
        question=str(payload["question"]),
        expected_status=str(expected["status"]),
        expected_route=str(expected["route"]),
        expected_tools=tuple(map(str, expected.get("tools", []))),
        expected_ratios=ratios,
        expected_citation_sources=tuple(
            map(str, expected.get("citation_sources", []))
        ),
        expected_evidence_requirements=tuple(
            map(str, expected.get("evidence_requirements", []))
        ),
        expected_top_section=(
            str(expected["retrieval"]["top_section"])
            if expected.get("retrieval", {}).get("top_section")
            else None
        ),
        expected_retrieval_terms=tuple(
            map(str, expected.get("retrieval", {}).get("required_terms", []))
        ),
        expected_grounded_terms=tuple(
            map(str, expected.get("grounded_terms", []))
        ),
        expected_limitation_terms=tuple(
            map(str, expected.get("limitation_terms", []))
        ),
    )


def _evaluate_case(
    case: EvaluationCase,
    agent: ResearchAgent,
) -> EvaluationCaseResult:
    answer = agent.answer(case.ticker, case.question)
    actual_tools = tuple(call.tool.value for call in answer.plan.tool_calls)
    contract_passed = (
        answer.status.value == case.expected_status
        and answer.plan.route.value == case.expected_route
        and actual_tools == case.expected_tools
    )
    checks = {
        "sql_or_tool_outputs": EvaluationCheck(
            passed=contract_passed,
            detail=(
                f"status={answer.status.value}; route={answer.plan.route.value}; "
                f"tools={list(actual_tools)}"
            ),
        )
    }

    if case.expected_ratios:
        actual_ratios = (
            answer.financial.report.snapshot.ratios
            if answer.financial is not None
            else {}
        )
        ratio_results = {
            name: (
                name in actual_ratios
                and math.isclose(
                    float(actual_ratios[name]),
                    expectation.value,
                    rel_tol=0.0,
                    abs_tol=expectation.absolute_tolerance,
                )
            )
            for name, expectation in case.expected_ratios.items()
        }
        checks["numeric_accuracy"] = EvaluationCheck(
            passed=all(ratio_results.values()),
            detail=f"ratios={actual_ratios}; matched={ratio_results}",
        )

    if case.expected_citation_sources:
        observed_sources = _observed_citation_sources(answer)
        checks["citation_presence"] = EvaluationCheck(
            passed=set(case.expected_citation_sources).issubset(observed_sources),
            detail=f"observed={sorted(observed_sources)}",
        )

    if case.expected_top_section or case.expected_retrieval_terms:
        top_match = (
            answer.filing.matches[0]
            if answer.filing is not None and answer.filing.matches
            else None
        )
        top_section = top_match.chunk.section if top_match is not None else None
        top_text = top_match.chunk.text.lower() if top_match is not None else ""
        matched_terms = tuple(
            term
            for term in case.expected_retrieval_terms
            if term.lower() in top_text
        )
        section_matches = (
            case.expected_top_section is None
            or top_section == case.expected_top_section
        )
        checks["retrieval_relevance"] = EvaluationCheck(
            passed=(
                section_matches
                and matched_terms == case.expected_retrieval_terms
            ),
            detail=(
                f"top_section={top_section}; matched_terms={list(matched_terms)}"
            ),
        )

    if case.expected_evidence_requirements or case.expected_grounded_terms:
        satisfied = tuple(requirement.value for requirement in answer.satisfied_evidence)
        evidence_text = " ".join(
            match.chunk.text.lower()
            for match in answer.filing.matches
        ) if answer.filing is not None else ""
        grounded_terms = tuple(
            term
            for term in case.expected_grounded_terms
            if term.lower() in evidence_text
        )
        checks["groundedness"] = EvaluationCheck(
            passed=(
                satisfied == case.expected_evidence_requirements
                and grounded_terms == case.expected_grounded_terms
            ),
            detail=(
                f"satisfied={list(satisfied)}; "
                f"grounded_terms={list(grounded_terms)}"
            ),
        )

    if case.expected_status == "unsupported":
        limitation = answer.limitation or ""
        matched_limitation_terms = tuple(
            term
            for term in case.expected_limitation_terms
            if term.lower() in limitation.lower()
        )
        no_evidence = (
            answer.financial is None
            and answer.filing is None
            and not answer.satisfied_evidence
        )
        checks["unsupported_questions"] = EvaluationCheck(
            passed=(
                answer.status.value == "unsupported"
                and not actual_tools
                and no_evidence
                and matched_limitation_terms == case.expected_limitation_terms
            ),
            detail=(
                f"evidence={'none' if no_evidence else 'present'}; "
                f"limitation_terms={list(matched_limitation_terms)}"
            ),
        )

    return EvaluationCaseResult(
        case_id=case.case_id,
        passed=all(check.passed for check in checks.values()),
        checks=checks,
    )


def _observed_citation_sources(answer: ResearchAnswer) -> set[str]:
    observed: set[str] = set()
    if answer.financial is not None:
        for citation in answer.financial.report.citations:
            if "/api/xbrl/companyfacts/" in citation.url:
                observed.add("companyfacts")
            if "/Archives/edgar/data/" in citation.url:
                observed.add("filing")
    if answer.filing is not None:
        for match in answer.filing.matches:
            if "/Archives/edgar/data/" in match.chunk.source_url:
                observed.add("filing")
            if "/Archives/edgar/data/" in match.chunk.index_url:
                observed.add("filing_index")
    return observed
