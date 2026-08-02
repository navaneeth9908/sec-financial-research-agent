from pathlib import Path

from sec_financial_research.infrastructure.sec_client import SECClient, normalize_cik


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
