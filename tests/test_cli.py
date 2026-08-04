import json
import os
import time
import urllib.error
from pathlib import Path

from sec_financial_research import cli
from sec_financial_research.infrastructure.research_mart import DuckDBResearchMart
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


def test_cli_filings_fetches_recent_metadata_and_primary_documents(
    tmp_path: Path, monkeypatch, capsys
):
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

    def json_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                }
            }
        return submissions

    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=json_transport,
        text_transport=lambda url, headers, timeout: "<html>10-Q body</html>",
    )
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: client)

    exit_code = cli.main(["filings", "AAPL", "--limit", "1"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["company"] == {
        "ticker": "AAPL",
        "cik": "0000320193",
        "name": "Apple Inc.",
    }
    assert payload["submissions_url"] == (
        "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert payload["filings"] == [
        {
            "accession": "0000320193-25-000079",
            "form": "10-Q",
            "filing_date": "2025-08-01",
            "report_date": "2025-06-28",
            "primary_document_url": (
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019325000079/aapl-20250628.htm"
            ),
            "index_url": (
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019325000079/0000320193-25-000079-index.html"
            ),
            "document_characters": 22,
            "cache_status": "network",
        }
    ]


def test_cli_filing_chunks_outputs_a_cited_10k_sample(tmp_path: Path, monkeypatch, capsys):
    submissions = {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "filingDate": ["2025-10-31"],
                "reportDate": ["2025-09-27"],
                "form": ["10-K"],
                "primaryDocument": ["aapl-20250927.htm"],
            }
        },
    }

    def json_transport(url: str, headers: dict[str, str], timeout: float) -> dict:
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                }
            }
        return submissions

    filing_html = """
    <html><body>
      <h2>Item 1.</h2><p>1</p>
      <h2>Item 1A.</h2><p>5</p>
      <h2>Item 1. Business</h2>
      <p>Apple designs, manufactures and markets smartphones, personal computers,
      tablets, wearables and accessories, and sells related services.</p>
      <h2>Item 1A. Risk Factors</h2>
      <p>The Company's operations and performance depend substantially on global
      economic conditions and complex supply chains.</p>
    </body></html>
    """
    client = SECClient(
        user_agent="portfolio-agent contact@example.com",
        cache_dir=tmp_path,
        throttle_seconds=0,
        transport=json_transport,
        text_transport=lambda url, headers, timeout: filing_html,
    )
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: client)

    exit_code = cli.main(
        [
            "filing-chunks",
            "AAPL",
            "--max-chunks",
            "2",
            "--chunk-size",
            "100",
            "--overlap-chars",
            "20",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["filing"]["form"] == "10-K"
    assert payload["filing"]["accession"] == "0000320193-25-000079"
    assert payload["extraction"]["chunk_count"] >= 2
    assert payload["extraction"]["sections"] == [
        "Item 1 — Business",
        "Item 1A — Risk Factors",
    ]
    assert len(payload["sample_chunks"]) == 2
    assert [chunk["section"] for chunk in payload["sample_chunks"]] == [
        "Item 1 — Business",
        "Item 1A — Risk Factors",
    ]
    assert "designs, manufactures" in payload["sample_chunks"][0]["text"]
    assert "operations and performance" in payload["sample_chunks"][1]["text"]
    assert all(
        chunk["accession"] == "0000320193-25-000079"
        for chunk in payload["sample_chunks"]
    )
    assert all(
        chunk["source_url"] == payload["filing"]["primary_document_url"]
        for chunk in payload["sample_chunks"]
    )


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


def test_cli_mart_load_ingests_then_queries_the_company_slice(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(cli.SECClient, "from_env", lambda: ComparisonSECClient())
    database = tmp_path / "research.duckdb"

    exit_code = cli.main(
        ["mart-load", "AAPL", "--database", str(database)]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload == {
        "database": str(database),
        "ticker": "AAPL",
        "fiscal_end": "2025-09-27",
        "metric_rows": 5,
        "ratio_rows": 4,
        "source_url": (
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        ),
    }

    with DuckDBResearchMart(database) as mart:
        assert len(mart.company_metrics("AAPL")) == 5
        assert len(mart.company_ratios("AAPL")) == 4
