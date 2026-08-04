import json
import os
import time
import urllib.error
from pathlib import Path

import pytest

from sec_financial_research.infrastructure.sec_client import (
    SECClient,
    SECClientError,
    normalize_cik,
)


class FakeTransport:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> dict:
        self.urls.append(url)
        assert "User-Agent" in headers
        assert timeout == 12
        if url.endswith("company_tickers.json"):
            return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {}}}


def test_normalize_cik_zero_pads_to_ten_digits():
    assert normalize_cik(320193) == "0000320193"
    assert normalize_cik("0000320193") == "0000320193"


def test_client_resolves_ticker_and_caches_companyfacts(tmp_path: Path):
    transport = FakeTransport()
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        cache_ttl_seconds=3600,
        timeout_seconds=12,
        throttle_seconds=0,
        transport=transport,
    )

    identity = client.resolve_ticker("aapl")
    first = client.get_company_facts(identity.cik)
    second = client.get_company_facts(identity.cik)

    assert identity.ticker == "AAPL"
    assert identity.cik == "0000320193"
    assert first == second
    assert len(transport.urls) == 2
    assert client.cache_metadata["company_tickers"].status == "network"
    assert client.cache_metadata["companyfacts_0000320193"].status == "fresh"


def test_recent_filings_are_filtered_and_retain_official_sec_links(tmp_path: Path):
    calls: list[str] = []
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-25-000079",
                    "0000320193-25-000081",
                    "0000320193-25-000057",
                ],
                "filingDate": ["2025-08-01", "2025-07-31", "2025-05-02"],
                "reportDate": ["2025-06-28", "2025-06-28", "2025-03-29"],
                "form": ["10-Q", "8-K", "10-Q"],
                "primaryDocument": ["aapl-20250628.htm", "aapl-8k.htm", "aapl-20250329.htm"],
            }
        },
    }

    def transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        calls.append(url)
        assert headers["User-Agent"] == "portfolio-agent contact@example.com"
        return submissions

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=transport,
    )

    first = client.get_recent_filings("320193", limit=1)
    second = client.get_recent_filings("0000320193", limit=1)

    assert first == second
    assert len(first) == 1
    filing = first[0]
    assert filing.accession == "0000320193-25-000079"
    assert filing.form == "10-Q"
    assert filing.filing_date == "2025-08-01"
    assert filing.report_date == "2025-06-28"
    assert filing.submissions_url == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert filing.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250628.htm"
    )
    assert filing.index_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/0000320193-25-000079-index.html"
    )
    assert calls == ["https://data.sec.gov/submissions/CIK0000320193.json"]
    assert client.cache_metadata["submissions_0000320193"].status == "fresh"


def test_primary_filing_document_is_cached_with_accession_provenance(tmp_path: Path):
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "filingDate": ["2025-08-01"],
                "reportDate": ["2025-06-28"],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-20250628.htm"],
            }
        },
    }
    document_html = "<html><body><h1>Apple 2025 Q3 Form 10-Q</h1></body></html>"
    text_calls: list[str] = []

    def text_transport(url: str, headers: dict[str, str], timeout: float) -> str:
        text_calls.append(url)
        assert headers["User-Agent"] == "portfolio-agent contact@example.com"
        assert headers["Accept"] == "text/html,application/xhtml+xml"
        return document_html

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=lambda url, headers, timeout: submissions,
        text_transport=text_transport,
    )
    filing = client.get_recent_filings("320193", limit=1)[0]

    first = client.get_filing_document(filing)
    second = client.get_filing_document(filing)

    assert first == second
    assert first.filing == filing
    assert first.text == document_html
    assert first.source_url == filing.primary_document_url
    assert text_calls == [filing.primary_document_url]
    assert client.cache_metadata[
        "filing_0000320193-25-000079_aapl-20250628.htm"
    ].status == "fresh"


def test_recent_filings_reject_accession_from_a_different_cik(tmp_path: Path):
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-25-000079"],
                "filingDate": ["2025-08-01"],
                "reportDate": ["2025-06-28"],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-20250628.htm"],
            }
        },
    }
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=lambda url, headers, timeout: submissions,
    )

    with pytest.raises(SECClientError, match="does not belong to CIK 0000320193"):
        client.get_recent_filings("320193")


def test_recent_filings_reject_unsafe_primary_document_path(tmp_path: Path):
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "filingDate": ["2025-08-01"],
                "reportDate": ["2025-06-28"],
                "form": ["10-Q"],
                "primaryDocument": ["../aapl-20250628.htm"],
            }
        },
    }
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=lambda url, headers, timeout: submissions,
    )

    with pytest.raises(SECClientError, match="unsafe primary document"):
        client.get_recent_filings("320193")


def test_recent_filings_require_a_positive_limit(tmp_path: Path):
    calls = 0

    def transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        nonlocal calls
        calls += 1
        return {}

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=transport,
    )

    with pytest.raises(ValueError, match="limit must be at least 1"):
        client.get_recent_filings("320193", limit=0)

    assert calls == 0


def test_recent_filings_reject_submissions_for_a_different_cik(tmp_path: Path):
    submissions = {
        "cik": "0001045810",
        "name": "Wrong issuer",
        "filings": {"recent": {}},
    }
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=lambda url, headers, timeout: submissions,
    )

    with pytest.raises(SECClientError, match="returned CIK 0001045810"):
        client.get_recent_filings("320193")


def test_transient_http_error_is_retried_before_network_success(tmp_path: Path, monkeypatch):
    attempts = 0
    payload = {"cik": 320193, "entityName": "Apple Inc."}

    def recovering_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)
        return payload

    monkeypatch.setattr(
        "sec_financial_research.infrastructure.sec_client.time.sleep", lambda _: None
    )
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        max_attempts=3,
        transport=recovering_transport,
    )

    result = client.get_company_facts("320193")

    assert result == payload
    assert attempts == 2
    assert client.cache_metadata["companyfacts_0000320193"].status == "network"


def test_expired_cache_is_served_with_stale_metadata_after_transient_retries(
    tmp_path: Path, monkeypatch
):
    cache_path = tmp_path / "companyfacts_0000320193.json"
    cached_payload = {"cik": 320193, "entityName": "Cached Apple Inc."}
    cache_path.write_text(json.dumps(cached_payload), encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(cache_path, (stale_time, stale_time))
    attempts = 0

    def unavailable_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)

    monkeypatch.setattr(
        "sec_financial_research.infrastructure.sec_client.time.sleep", lambda _: None
    )
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        cache_ttl_seconds=10,
        throttle_seconds=0,
        max_attempts=3,
        transport=unavailable_transport,
    )

    payload = client.get_company_facts("320193")

    metadata = client.cache_metadata["companyfacts_0000320193"]
    assert payload == cached_payload
    assert attempts == 3
    assert metadata.status == "stale"
    assert metadata.age_seconds >= 60
    assert "HTTP Error 503" in metadata.refresh_error


def test_non_transient_http_error_fails_once_with_actionable_diagnostic(tmp_path: Path):
    attempts = 0

    def missing_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        max_attempts=3,
        transport=missing_transport,
    )

    with pytest.raises(SECClientError) as caught:
        client.get_company_facts("320193")

    assert attempts == 1
    assert "after 1 attempt" in str(caught.value)
    assert "HTTP 404" in str(caught.value)
    assert "no usable cached response" in str(caught.value)


def test_stale_cache_does_not_hide_a_non_transient_http_error(tmp_path: Path):
    cache_path = tmp_path / "companyfacts_0000320193.json"
    cache_path.write_text(json.dumps({"entityName": "Outdated"}), encoding="utf-8")
    stale_time = time.time() - 60
    os.utime(cache_path, (stale_time, stale_time))
    attempts = 0

    def missing_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        cache_ttl_seconds=10,
        throttle_seconds=0,
        max_attempts=3,
        transport=missing_transport,
    )

    with pytest.raises(SECClientError) as caught:
        client.get_company_facts("320193")

    assert attempts == 1
    assert "stale cache was not used for non-transient HTTP 404" in str(caught.value)


def test_corrupt_cache_is_replaced_by_a_network_response(tmp_path: Path):
    cache_path = tmp_path / "companyfacts_0000320193.json"
    cache_path.write_text("{not-json", encoding="utf-8")
    network_payload = {"cik": 320193, "entityName": "Apple Inc."}

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=lambda url, headers, timeout: network_payload,
    )

    payload = client.get_company_facts("320193")

    assert payload == network_payload
    assert json.loads(cache_path.read_text(encoding="utf-8")) == network_payload
    assert client.cache_metadata["companyfacts_0000320193"].status == "network"
