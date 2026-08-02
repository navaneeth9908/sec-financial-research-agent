"""Resilient SEC EDGAR adapter with throttling, retry, and atomic disk cache."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sec_financial_research.domain.models import CompanyIdentity

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
Transport = Callable[[str, dict[str, str], float], dict[str, Any]]


class SECClientError(RuntimeError):
    """Raised for unavailable or invalid SEC data."""


def normalize_cik(cik: str | int) -> str:
    digits = str(cik).strip()
    if not digits.isdigit() or len(digits) > 10:
        raise ValueError(f"Invalid SEC CIK: {cik!r}")
    return digits.zfill(10)


def _urllib_transport(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
        self._last_request_at = 0.0

    @classmethod
    def from_env(cls) -> "SECClient":
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

    def _cached_json(self, cache_key: str, url: str) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{cache_key}.json"
        if path.exists() and time.time() - path.stat().st_mtime <= self.cache_ttl_seconds:
            return json.loads(path.read_text(encoding="utf-8"))

        payload = self._request_json(url)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        return payload

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
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
        raise SECClientError(f"SEC request failed for {url}: {last_error}") from last_error

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.throttle_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()
