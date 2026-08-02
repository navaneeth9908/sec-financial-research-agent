"""Normalize SEC XBRL facts and calculate deterministic financial analytics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sec_financial_research.domain.models import CompanyIdentity, FactPoint, FinancialSnapshot

CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "liabilities": ("Liabilities", "LiabilitiesCurrent"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
}
ANNUAL_END_MIN_DAYS = 330
ANNUAL_END_MAX_DAYS = 400


class FinancialDataError(ValueError):
    """Raised when required comparable SEC financial facts are unavailable."""


def annual_series(company_facts: dict, concept_names: Iterable[str]) -> list[FactPoint]:
    """Return unique annual 10-K facts sorted by period end.

    SEC can repeat a prior fiscal-year value in a later comparative 10-K. Values are
    deduplicated by period end and the most recently filed representation is kept.
    Preferred concepts win overlapping periods, while fallback concepts fill periods
    that the preferred concept does not cover.
    """
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    by_end: dict[str, FactPoint] = {}
    for concept in concept_names:
        payload = gaap.get(concept)
        if not payload:
            continue

        units = payload.get("units", {})
        raw_points = units.get("USD")
        if raw_points is None:
            raw_points = next(iter(units.values()), [])
        unit = "USD" if "USD" in units else next(iter(units), "unknown")

        concept_by_end: dict[str, FactPoint] = {}
        for raw in raw_points:
            if raw.get("form") not in {"10-K", "10-K/A"}:
                continue
            if raw.get("fp") != "FY" or raw.get("val") is None or not raw.get("end"):
                continue
            point = FactPoint(
                concept=concept,
                label=payload.get("label", concept),
                value=raw["val"],
                unit=unit,
                start=raw.get("start"),
                end=raw["end"],
                filed=raw.get("filed", ""),
                accession=raw.get("accn", ""),
                form=raw.get("form", "10-K"),
                fiscal_year=raw.get("fy"),
            )
            previous = concept_by_end.get(point.end)
            if previous is None or (point.filed, point.accession) > (
                previous.filed,
                previous.accession,
            ):
                concept_by_end[point.end] = point

        for end, point in concept_by_end.items():
            by_end.setdefault(end, point)

    return [by_end[end] for end in sorted(by_end)]


def _for_period(
    series: list[FactPoint], fiscal_end: str, metric_name: str
) -> FactPoint:
    exact = [point for point in series if point.end == fiscal_end]
    if exact:
        return exact[-1]
    raise FinancialDataError(
        f"No {metric_name} fact matches fiscal period {fiscal_end}"
    )


def _previous_comparable_revenue_period(
    series: list[FactPoint], latest: FactPoint
) -> FactPoint:
    latest_end = date.fromisoformat(latest.end)
    for point in reversed(series[:-1]):
        days_between_ends = (latest_end - date.fromisoformat(point.end)).days
        if ANNUAL_END_MIN_DAYS <= days_between_ends <= ANNUAL_END_MAX_DAYS:
            return point
    raise FinancialDataError(
        f"No comparable prior annual revenue period for {latest.end}"
    )


def _percent(numerator: float, denominator: float, label: str) -> float:
    if denominator == 0:
        raise FinancialDataError(f"Cannot calculate {label} with a zero denominator")
    return numerator / denominator * 100


def build_financial_snapshot(
    identity: CompanyIdentity,
    company_facts: dict,
) -> FinancialSnapshot:
    """Build the latest annual company snapshot and derived ratios."""
    series = {
        key: annual_series(company_facts, aliases)
        for key, aliases in CONCEPTS.items()
    }
    missing = [key for key, points in series.items() if not points]
    if missing:
        raise FinancialDataError(f"Missing required SEC concepts: {', '.join(missing)}")
    if len(series["revenue"]) < 2:
        raise FinancialDataError("At least two annual revenue periods are required")

    latest_revenue = series["revenue"][-1]
    previous_revenue = _previous_comparable_revenue_period(
        series["revenue"], latest_revenue
    )
    fiscal_end = latest_revenue.end
    metrics = {
        key: _for_period(points, fiscal_end, key)
        for key, points in series.items()
    }

    revenue = float(metrics["revenue"].value)
    net_income = float(metrics["net_income"].value)
    assets = float(metrics["assets"].value)
    liabilities = float(metrics["liabilities"].value)
    operating_cash_flow = float(metrics["operating_cash_flow"].value)

    ratios = {
        "revenue_growth_pct": _percent(
            revenue - float(previous_revenue.value),
            float(previous_revenue.value),
            "revenue growth",
        ),
        "net_margin_pct": _percent(net_income, revenue, "net margin"),
        "liabilities_to_assets_pct": _percent(
            liabilities, assets, "liabilities to assets"
        ),
        "cash_conversion_pct": _percent(
            operating_cash_flow, net_income, "cash conversion"
        ),
    }
    fiscal_year = latest_revenue.fiscal_year or int(fiscal_end[:4])
    return FinancialSnapshot(
        identity=identity,
        fiscal_year=fiscal_year,
        fiscal_end=fiscal_end,
        metrics=metrics,
        ratios=ratios,
    )
