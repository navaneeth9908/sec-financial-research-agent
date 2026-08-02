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
