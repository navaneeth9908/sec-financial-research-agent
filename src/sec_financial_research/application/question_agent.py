"""Deterministic planning and orchestration for evidence-backed research questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sec_financial_research.ai.tools import (
    EvidenceRequirementError,
    FilingRetrievalResult,
    FilingRetrievalTool,
    FinancialAnalyticsResult,
    FinancialAnalyticsTool,
)

_FINANCIAL_TERMS = frozenset(
    {
        "assets",
        "cash",
        "financial",
        "growth",
        "income",
        "liabilities",
        "margin",
        "ratio",
        "revenue",
    }
)
_FILING_TERMS = frozenset(
    {
        "business",
        "competition",
        "cybersecurity",
        "disclose",
        "disclosed",
        "disclosure",
        "filing",
        "risk",
        "risks",
        "strategy",
        "supply",
    }
)
_UNSUPPORTED_RECOMMENDATION_TERMS = frozenset(
    {"buy", "recommend", "recommendation", "sell"}
)
_UNSUPPORTED_FORECAST_TERMS = frozenset(
    {"forecast", "forecasts", "predict", "prediction", "projection"}
)


class ResearchToolName(StrEnum):
    """Typed names for deterministic research capabilities."""

    FINANCIAL_ANALYTICS = "financial_analytics"
    FILING_RETRIEVAL = "filing_retrieval"


class EvidenceRequirement(StrEnum):
    """Evidence gates a plan must satisfy before an answer is supported."""

    CALCULATED_FINANCIALS = "calculated_financials"
    COMPANYFACTS_CITATION = "companyfacts_citation"
    FILING_CHUNK_CITATION = "filing_chunk_citation"


class QuestionRoute(StrEnum):
    """Question classes supported by the deterministic planner."""

    FINANCIAL = "financial"
    FILING = "filing"
    COMBINED = "combined"
    UNSUPPORTED = "unsupported"


class AnswerStatus(StrEnum):
    """Whether every evidence gate for a planned answer was satisfied."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PlannedToolCall:
    tool: ResearchToolName
    objective: str


@dataclass(frozen=True)
class ResearchPlan:
    route: QuestionRoute
    tool_calls: tuple[PlannedToolCall, ...]
    evidence_requirements: tuple[EvidenceRequirement, ...]
    limitation: str | None = None


@dataclass(frozen=True)
class ResearchAnswer:
    question: str
    ticker: str
    status: AnswerStatus
    plan: ResearchPlan
    satisfied_evidence: tuple[EvidenceRequirement, ...]
    financial: FinancialAnalyticsResult | None = None
    filing: FilingRetrievalResult | None = None
    limitation: str | None = None


class DeterministicQuestionPlanner:
    """Build an auditable tool plan without delegating routing to an LLM."""

    def plan(self, question: str) -> ResearchPlan:
        terms = set(re.findall(r"[a-z0-9]+", question.lower()))
        if terms.intersection(_UNSUPPORTED_RECOMMENDATION_TERMS):
            return ResearchPlan(
                route=QuestionRoute.UNSUPPORTED,
                tool_calls=(),
                evidence_requirements=(),
                limitation=(
                    "investment recommendations are outside the supported scope; "
                    "ask for cited historical financials or filing disclosures."
                ),
            )
        if terms.intersection(_UNSUPPORTED_FORECAST_TERMS):
            return ResearchPlan(
                route=QuestionRoute.UNSUPPORTED,
                tool_calls=(),
                evidence_requirements=(),
                limitation=(
                    "forecasts are outside the supported scope; ask for cited "
                    "historical financials or filing disclosures."
                ),
            )
        has_financial_intent = bool(terms.intersection(_FINANCIAL_TERMS))
        has_filing_intent = bool(terms.intersection(_FILING_TERMS))
        if not has_financial_intent and not has_filing_intent:
            return ResearchPlan(
                route=QuestionRoute.UNSUPPORTED,
                tool_calls=(),
                evidence_requirements=(),
                limitation=(
                    "Question is outside the supported SEC financial and filing "
                    "research scope."
                ),
            )
        if has_financial_intent and not has_filing_intent:
            return ResearchPlan(
                route=QuestionRoute.FINANCIAL,
                tool_calls=(
                    PlannedToolCall(
                        tool=ResearchToolName.FINANCIAL_ANALYTICS,
                        objective="Calculate financial metrics from normalized SEC facts.",
                    ),
                ),
                evidence_requirements=(
                    EvidenceRequirement.CALCULATED_FINANCIALS,
                    EvidenceRequirement.COMPANYFACTS_CITATION,
                ),
            )
        if has_filing_intent and not has_financial_intent:
            return ResearchPlan(
                route=QuestionRoute.FILING,
                tool_calls=(
                    PlannedToolCall(
                        tool=ResearchToolName.FILING_RETRIEVAL,
                        objective="Retrieve relevant filing passages with SEC citations.",
                    ),
                ),
                evidence_requirements=(
                    EvidenceRequirement.FILING_CHUNK_CITATION,
                ),
            )
        return ResearchPlan(
            route=QuestionRoute.COMBINED,
            tool_calls=(
                PlannedToolCall(
                    tool=ResearchToolName.FINANCIAL_ANALYTICS,
                    objective="Calculate financial metrics from normalized SEC facts.",
                ),
                PlannedToolCall(
                    tool=ResearchToolName.FILING_RETRIEVAL,
                    objective="Retrieve relevant filing passages with SEC citations.",
                ),
            ),
            evidence_requirements=(
                EvidenceRequirement.CALCULATED_FINANCIALS,
                EvidenceRequirement.COMPANYFACTS_CITATION,
                EvidenceRequirement.FILING_CHUNK_CITATION,
            ),
        )


class ResearchAgent:
    """Execute a deterministic plan and release output only after evidence gates pass."""

    def __init__(
        self,
        *,
        financial_tool: FinancialAnalyticsTool,
        filing_tool: FilingRetrievalTool,
        planner: DeterministicQuestionPlanner | None = None,
    ) -> None:
        self._financial_tool = financial_tool
        self._filing_tool = filing_tool
        self._planner = planner or DeterministicQuestionPlanner()

    def answer(
        self,
        ticker: str,
        question: str,
        *,
        filing_limit: int = 3,
    ) -> ResearchAnswer:
        normalized_ticker = ticker.strip().upper()
        normalized_question = question.strip()
        plan = self._planner.plan(normalized_question)
        if plan.route is QuestionRoute.UNSUPPORTED:
            return ResearchAnswer(
                question=normalized_question,
                ticker=normalized_ticker,
                status=AnswerStatus.UNSUPPORTED,
                plan=plan,
                satisfied_evidence=(),
                limitation=plan.limitation,
            )

        financial: FinancialAnalyticsResult | None = None
        filing: FilingRetrievalResult | None = None
        try:
            for call in plan.tool_calls:
                if call.tool is ResearchToolName.FINANCIAL_ANALYTICS:
                    financial = self._financial_tool.run(normalized_ticker)
                elif call.tool is ResearchToolName.FILING_RETRIEVAL:
                    filing = self._filing_tool.run(
                        normalized_ticker,
                        normalized_question,
                        limit=filing_limit,
                    )
        except EvidenceRequirementError as exc:
            return ResearchAnswer(
                question=normalized_question,
                ticker=normalized_ticker,
                status=AnswerStatus.UNSUPPORTED,
                plan=plan,
                satisfied_evidence=(),
                limitation=str(exc),
            )

        return ResearchAnswer(
            question=normalized_question,
            ticker=normalized_ticker,
            status=AnswerStatus.SUPPORTED,
            plan=plan,
            satisfied_evidence=plan.evidence_requirements,
            financial=financial,
            filing=filing,
        )
