"""DuckDB-backed analytical mart for normalized SEC research snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import duckdb

from sec_financial_research.domain.models import FinancialSnapshot

CALCULATION_VERSION = "financial_snapshot_v1"


class DataQualityError(ValueError):
    """Raised when a snapshot fails a pre-ingestion quality gate."""


class DuckDBResearchMart:
    """Persist normalized financial snapshots for repeatable analytical queries."""

    def __init__(self, database: str | Path) -> None:
        database_path = str(database)
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(database_path)
        self._create_schema()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_metric_facts (
                cik VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                company_name VARCHAR NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_end DATE NOT NULL,
                metric_name VARCHAR NOT NULL,
                metric_value DOUBLE NOT NULL,
                metric_unit VARCHAR NOT NULL,
                concept VARCHAR NOT NULL,
                accession VARCHAR NOT NULL,
                form VARCHAR NOT NULL,
                filed DATE NOT NULL,
                source_url VARCHAR NOT NULL,
                ingested_at TIMESTAMP NOT NULL,
                PRIMARY KEY (cik, fiscal_end, metric_name)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_ratios (
                cik VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                company_name VARCHAR NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_end DATE NOT NULL,
                ratio_name VARCHAR NOT NULL,
                ratio_value_pct DOUBLE NOT NULL,
                calculation_version VARCHAR NOT NULL,
                source_accessions VARCHAR[] NOT NULL,
                source_url VARCHAR NOT NULL,
                ingested_at TIMESTAMP NOT NULL,
                PRIMARY KEY (cik, fiscal_end, ratio_name)
            )
            """
        )

    def ingest_snapshot(
        self,
        snapshot: FinancialSnapshot,
        *,
        source_url: str,
        ingested_at: str | datetime | None = None,
    ) -> None:
        """Replace one company-period slice atomically, preventing duplicate rows."""
        expected_source_url = (
            "https://data.sec.gov/api/xbrl/companyfacts/"
            f"CIK{snapshot.identity.cik}.json"
        )
        if source_url != expected_source_url:
            raise DataQualityError(
                "source URL must match official SEC Companyfacts endpoint "
                f"{expected_source_url}"
            )
        for metric_name, point in snapshot.metrics.items():
            if point.end != snapshot.fiscal_end:
                raise DataQualityError(
                    f"{metric_name} period {point.end} does not match snapshot period "
                    f"{snapshot.fiscal_end}"
                )
            if not point.accession.startswith(f"{snapshot.identity.cik}-"):
                raise DataQualityError(
                    f"{metric_name} accession does not belong to CIK "
                    f"{snapshot.identity.cik}"
                )

        ingestion_time = ingested_at or datetime.now(UTC)
        identity = snapshot.identity
        metric_rows = [
            (
                identity.cik,
                identity.ticker,
                identity.name,
                snapshot.fiscal_year,
                snapshot.fiscal_end,
                metric_name,
                float(point.value),
                point.unit,
                point.concept,
                point.accession,
                point.form,
                point.filed,
                source_url,
                ingestion_time,
            )
            for metric_name, point in snapshot.metrics.items()
        ]
        source_accessions = sorted(
            {point.accession for point in snapshot.metrics.values()}
        )
        ratio_rows = [
            (
                identity.cik,
                identity.ticker,
                identity.name,
                snapshot.fiscal_year,
                snapshot.fiscal_end,
                ratio_name,
                float(value),
                CALCULATION_VERSION,
                source_accessions,
                source_url,
                ingestion_time,
            )
            for ratio_name, value in snapshot.ratios.items()
        ]

        self._connection.execute("BEGIN TRANSACTION")
        try:
            key = [identity.cik, snapshot.fiscal_end]
            self._connection.execute(
                "DELETE FROM financial_ratios WHERE cik = ? AND fiscal_end = ?", key
            )
            self._connection.execute(
                "DELETE FROM financial_metric_facts WHERE cik = ? AND fiscal_end = ?",
                key,
            )
            self._connection.executemany(
                """
                INSERT INTO financial_metric_facts VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                metric_rows,
            )
            self._connection.executemany(
                """
                INSERT INTO financial_ratios VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                ratio_rows,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def company_metrics(self, ticker: str) -> list[dict[str, Any]]:
        """Return all normalized fact rows for a ticker with source lineage."""
        return self._query_rows(
            """
            SELECT *
            FROM financial_metric_facts
            WHERE ticker = upper(?)
            ORDER BY fiscal_end DESC, metric_name
            """,
            [ticker.strip()],
        )

    def company_ratios(self, ticker: str) -> list[dict[str, Any]]:
        """Return all calculated ratio rows for a ticker with derivation lineage."""
        return self._query_rows(
            """
            SELECT *
            FROM financial_ratios
            WHERE ticker = upper(?)
            ORDER BY fiscal_end DESC, ratio_name
            """,
            [ticker.strip()],
        )

    def _query_rows(self, query: str, parameters: list[Any]) -> list[dict[str, Any]]:
        cursor = self._connection.execute(query, parameters)
        columns = [column[0] for column in cursor.description]
        return [
            dict(zip(columns, row, strict=True))
            for row in cursor.fetchall()
        ]
