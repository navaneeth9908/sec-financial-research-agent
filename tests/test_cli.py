import json
import os
import time
import urllib.error
from pathlib import Path

from sec_financial_research import cli
from sec_financial_research.infrastructure.sec_client import SECClient
from tests.test_comparison import ComparisonSECClient

FIXTURE = Path(__file__).parent / "fixtures" / "apple_companyfacts_min.json"


def test_cli_warns_when_stale_sec_cache_is_used(tmp_path: Path, monkeypatch, capsys):
    ticker_cache = tmp_path / "company_tickers.json"
    ticker_cache.write_text(
        json.dumps(
            {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        ),
        encoding="utf-8",
    )
    facts_cache = tmp_path / "companyfacts_0000320193.json"
    facts_cache.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    stale_time = time.time() - 60
    for path in (ticker_cache, facts_cache):
        os.utime(path, (stale_time, stale_time))

    def unavailable_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        cache_ttl_seconds=10,
        throttle_seconds=0,
        max_attempts=1,
        transport=unavailable_transport,
    )
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: client)

    exit_code = cli.main(["report", "AAPL", "--format", "json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["company"]["ticker"] == "AAPL"
    assert "warning: stale SEC cache used for company_tickers" in captured.err
    assert "warning: stale SEC cache used for companyfacts_0000320193" in captured.err
    assert "refresh failed after 1 attempt" in captured.err


def test_cli_reports_sec_failure_when_no_cache_is_available(tmp_path: Path, monkeypatch, capsys):
    def missing_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        max_attempts=3,
        transport=missing_transport,
    )
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: client)

    exit_code = cli.main(["report", "AAPL"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "error: SEC request failed after 1 attempt" in captured.err
    assert "HTTP 404 Not Found" in captured.err
    assert "no usable cached response" in captured.err


def test_cli_compare_outputs_normalized_ranked_cited_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.SECClient, "from_env", lambda: ComparisonSECClient()
    )

    exit_code = cli.main(["compare", "AAPL", "NVDA", "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert [company["company"]["ticker"] for company in payload["companies"]] == [
        "AAPL",
        "NVDA",
    ]
    assert payload["normalization"]["monetary_unit"] == "USD billions"
    assert payload["ratio_rankings"]["net_margin_pct"][0]["ticker"] == "NVDA"
    assert payload["companies"][0]["citations"][1]["url"].startswith(
        "https://www.sec.gov/Archives/edgar/data/"
    )
