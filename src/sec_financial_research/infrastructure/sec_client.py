"""Resilient SEC EDGAR adapter with throttling, retry, and atomic disk cache."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sec_financial_research.domain.models import (
    CompanyIdentity,
    FilingDocument,
    FilingMetadata,
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
Transport = Callable[[str, dict[str, str], float], dict[str, Any]]
TextTransport = Callable[[str, dict[str, str], float], str]


class SECClientError(RuntimeError):
    """Raised for unavailable or invalid SEC data."""


@dataclass(frozen=True)
class CacheMetadata:
    """Describes how a cached SEC response was resolved."""

    cache_key: str
    status: str
    age_seconds: float
    refresh_error: str | None = None


def normalize_cik(cik: str | int) -> str:
    digits = str(cik).strip()
    if not digits.isdigit() or len(digits) > 10:
        raise ValueError(f"Invalid SEC CIK: {cik!r}")
    return digits.zfill(10)


def _urllib_transport(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _urllib_text_transport(url: str, headers: dict[str, str], timeout: float) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


class SECClient:
    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: str | Path = ".cache/sec",
        cache_ttl_seconds: int = 21_600,
        timeout_seconds: float = 30,
        throttle_seconds: float = 0.12,
        max_attempts: int = 3,
        transport: Transport | None = None,
        text_transport: TextTransport | None = None,
    ) -> None:
        if "@" not in user_agent or len(user_agent.strip()) < 12:
            raise ValueError(
                "SEC_USER_AGENT must identify the application and include a contact email"
            )
        self.user_agent = user_agent.strip()
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.throttle_seconds = max(0.0, float(throttle_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.transport = transport or _urllib_transport
        self.text_transport = text_transport or _urllib_text_transport
        self.cache_metadata: dict[str, CacheMetadata] = {}
        self._last_request_at = 0.0

    @classmethod
    def from_env(cls) -> SECClient:
        return cls(
            user_agent=os.getenv(
                "SEC_USER_AGENT",
                "navaneeth9908/sec-financial-research-agent "
                "navaneeththota410@gmail.com",
            ),
            cache_dir=os.getenv("SEC_CACHE_DIR", ".cache/sec"),
            cache_ttl_seconds=int(os.getenv("SEC_CACHE_TTL_SECONDS", "21600")),
            timeout_seconds=float(os.getenv("SEC_HTTP_TIMEOUT_SECONDS", "30")),
        )

    def resolve_ticker(self, ticker: str) -> CompanyIdentity:
        normalized = ticker.strip().upper()
        registry = self._cached_json("company_tickers", SEC_TICKERS_URL)
        for entry in registry.values():
            if str(entry.get("ticker", "")).upper() == normalized:
                return CompanyIdentity(
                    ticker=normalized,
                    cik=normalize_cik(entry["cik_str"]),
                    name=entry["title"],
                )
        raise SECClientError(f"Ticker {normalized!r} was not found in the SEC registry")

    def get_company_facts(self, cik: str | int) -> dict[str, Any]:
        normalized = normalize_cik(cik)
        return self._cached_json(
            f"companyfacts_{normalized}",
            SEC_COMPANYFACTS_URL.format(cik=normalized),
        )

    def get_recent_filings(
        self,
        cik: str | int,
        *,
        forms: tuple[str, ...] = ("10-K", "10-Q"),
        limit: int = 10,
    ) -> tuple[FilingMetadata, ...]:
        if limit < 1:
            raise ValueError("Filing limit must be at least 1")
        normalized = normalize_cik(cik)
        submissions_url = SEC_SUBMISSIONS_URL.format(cik=normalized)
        payload = self._cached_json(f"submissions_{normalized}", submissions_url)
        payload_cik = normalize_cik(payload.get("cik", ""))
        if payload_cik != normalized:
            raise SECClientError(
                f"SEC submissions for CIK {normalized} returned CIK {payload_cik}"
            )
        recent = payload["filings"]["recent"]
        filings: list[FilingMetadata] = []
        for accession, form, filing_date, report_date, primary_document in zip(
            recent["accessionNumber"],
            recent["form"],
            recent["filingDate"],
            recent["reportDate"],
            recent["primaryDocument"],
            strict=True,
        ):
            if form not in forms:
                continue
            if not accession.startswith(f"{normalized}-"):
                raise SECClientError(
                    f"Filing accession {accession!r} does not belong to CIK {normalized}"
                )
            if not primary_document or Path(primary_document).name != primary_document:
                raise SECClientError(
                    f"Filing {accession!r} has an unsafe primary document path"
                )
            accession_compact = accession.replace("-", "")
            archive_base = (
                "https://www.sec.gov/Archives/edgar/data/"
                f"{int(normalized)}/{accession_compact}"
            )
            filings.append(
                FilingMetadata(
                    cik=normalized,
                    company_name=payload["name"],
                    accession=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=report_date,
                    primary_document=primary_document,
                    submissions_url=submissions_url,
                    primary_document_url=f"{archive_base}/{primary_document}",
                    index_url=f"{archive_base}/{accession}-index.html",
                )
            )
            if len(filings) == limit:
                break
        return tuple(filings)

    def get_filing_document(self, filing: FilingMetadata) -> FilingDocument:
        cache_key = f"filing_{filing.accession}_{filing.primary_document}"
        text = self._cached_text(cache_key, filing.primary_document_url)
        return FilingDocument(
            filing=filing,
            text=text,
            source_url=filing.primary_document_url,
        )

    def _cached_text(self, cache_key: str, url: str) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{cache_key}.txt"
        cached_payload: str | None = None
        age_seconds = 0.0
        if path.exists():
            age_seconds = max(0.0, time.time() - path.stat().st_mtime)
            cached_payload = path.read_text(encoding="utf-8")
            if age_seconds <= self.cache_ttl_seconds:
                self.cache_metadata[cache_key] = CacheMetadata(
                    cache_key=cache_key,
                    status="fresh",
                    age_seconds=age_seconds,
                )
                return cached_payload

        try:
            payload = self._request_text(url)
        except SECClientError as exc:
            if cached_payload is None:
                raise SECClientError(
                    f"{exc}; no usable cached response is available"
                ) from exc
            cause = exc.__cause__
            if (
                isinstance(cause, urllib.error.HTTPError)
                and cause.code not in TRANSIENT_STATUS_CODES
            ):
                raise SECClientError(
                    f"{exc}; stale cache was not used for non-transient "
                    f"HTTP {cause.code}"
                ) from exc
            self.cache_metadata[cache_key] = CacheMetadata(
                cache_key=cache_key,
                status="stale",
                age_seconds=age_seconds,
                refresh_error=str(exc),
            )
            return cached_payload
        temporary = path.with_suffix(".txt.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        self.cache_metadata[cache_key] = CacheMetadata(
            cache_key=cache_key,
            status="network",
            age_seconds=0.0,
        )
        return payload

    def _cached_json(self, cache_key: str, url: str) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{cache_key}.json"
        cached_payload: dict[str, Any] | None = None
        age_seconds = 0.0
        if path.exists():
            age_seconds = max(0.0, time.time() - path.stat().st_mtime)
            try:
                cached_payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached_payload = None
            if cached_payload is not None and age_seconds <= self.cache_ttl_seconds:
                self.cache_metadata[cache_key] = CacheMetadata(
                    cache_key=cache_key,
                    status="fresh",
                    age_seconds=age_seconds,
                )
                return cached_payload

        try:
            payload = self._request_json(url)
        except SECClientError as exc:
            if cached_payload is None:
                raise SECClientError(
                    f"{exc}; no usable cached response is available"
                ) from exc
            cause = exc.__cause__
            if (
                isinstance(cause, urllib.error.HTTPError)
                and cause.code not in TRANSIENT_STATUS_CODES
            ):
                raise SECClientError(
                    f"{exc}; stale cache was not used for non-transient "
                    f"HTTP {cause.code}"
                ) from exc
            self.cache_metadata[cache_key] = CacheMetadata(
                cache_key=cache_key,
                status="stale",
                age_seconds=age_seconds,
                refresh_error=str(exc),
            )
            return cached_payload
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        self.cache_metadata[cache_key] = CacheMetadata(
            cache_key=cache_key,
            status="network",
            age_seconds=0.0,
        )
        return payload

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None
        attempts_made = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts_made = attempt
            self._throttle()
            try:
                return self.transport(url, headers, self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in TRANSIENT_STATUS_CODES or attempt == self.max_attempts:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    break
            time.sleep(min(2 ** (attempt - 1), 4))
        attempt_label = "attempt" if attempts_made == 1 else "attempts"
        if isinstance(last_error, urllib.error.HTTPError):
            detail = (
                f"HTTP {last_error.code} {last_error.reason} "
                f"({last_error})"
            )
        else:
            detail = str(last_error)
        raise SECClientError(
            f"SEC request failed after {attempts_made} {attempt_label} "
            f"for {url}: {detail}"
        ) from last_error

    def _request_text(self, url: str) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None
        attempts_made = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts_made = attempt
            self._throttle()
            try:
                return self.text_transport(url, headers, self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in TRANSIENT_STATUS_CODES or attempt == self.max_attempts:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    break
            time.sleep(min(2 ** (attempt - 1), 4))
        attempt_label = "attempt" if attempts_made == 1 else "attempts"
        if isinstance(last_error, urllib.error.HTTPError):
            detail = f"HTTP {last_error.code} {last_error.reason} ({last_error})"
        else:
            detail = str(last_error)
        raise SECClientError(
            f"SEC request failed after {attempts_made} {attempt_label} "
            f"for {url}: {detail}"
        ) from last_error

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.throttle_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()
