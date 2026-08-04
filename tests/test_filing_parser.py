from sec_financial_research.domain.models import FilingDocument, FilingMetadata
from sec_financial_research.infrastructure.filing_parser import (
    chunk_filing_document,
    extract_filing_text,
)


def test_extract_filing_text_keeps_visible_structure_and_discards_inline_xbrl_noise():
    filing_html = """
    <html>
      <head><title>Apple Inc. 2025 Form 10-K</title></head>
      <body>
        <div style="display:none">Hidden inline XBRL fact</div>
        <h1>Apple Inc.</h1>
        <p>FORM 10-K</p>
        <h2>Item 1. Business</h2>
        <p>The Company designs, manufactures and markets smartphones.</p>
        <script>window.unrelated = true;</script>
      </body>
    </html>
    """

    text = extract_filing_text(filing_html)

    assert text.splitlines() == [
        "Apple Inc.",
        "FORM 10-K",
        "Item 1. Business",
        "The Company designs, manufactures and markets smartphones.",
    ]


def test_chunk_filing_document_keeps_sections_and_sec_citation_metadata():
    filing = FilingMetadata(
        cik="0000320193",
        company_name="Apple Inc.",
        accession="0000320193-25-000079",
        form="10-K",
        filing_date="2025-10-31",
        report_date="2025-09-27",
        primary_document="aapl-20250927.htm",
        submissions_url="https://data.sec.gov/submissions/CIK0000320193.json",
        primary_document_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/aapl-20250927.htm"
        ),
        index_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/0000320193-25-000079-index.html"
        ),
    )
    document = FilingDocument(
        filing=filing,
        source_url=filing.primary_document_url,
        text="""
        <html><body>
          <h2>Item 1. Business</h2>
          <p>Apple designs, manufactures and markets smartphones, personal computers,
          tablets, wearables and accessories, and sells related services.</p>
          <h2>Item 1A. Risk Factors</h2>
          <p>The Company's operations and performance depend substantially on global
          economic conditions and complex supply chains.</p>
        </body></html>
        """,
    )

    chunks = chunk_filing_document(document, max_chars=100, overlap_chars=20)

    assert {chunk.section for chunk in chunks} == {
        "Item 1 — Business",
        "Item 1A — Risk Factors",
    }
    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert all(chunk.accession == filing.accession for chunk in chunks)
    assert all(chunk.source_url == filing.primary_document_url for chunk in chunks)
    assert all(chunk.index_url == filing.index_url for chunk in chunks)
    assert all(
        "Risk Factors" not in chunk.text
        for chunk in chunks
        if chunk.section == "Item 1 — Business"
    )


def test_chunk_filing_document_uses_stable_labels_for_toc_and_body_headings():
    filing = FilingMetadata(
        cik="0000320193",
        company_name="Apple Inc.",
        accession="0000320193-25-000079",
        form="10-K",
        filing_date="2025-10-31",
        report_date="2025-09-27",
        primary_document="aapl-20250927.htm",
        submissions_url="https://data.sec.gov/submissions/CIK0000320193.json",
        primary_document_url="https://www.sec.gov/Archives/apple-10k.htm",
        index_url="https://www.sec.gov/Archives/apple-10k-index.html",
    )
    document = FilingDocument(
        filing=filing,
        source_url=filing.primary_document_url,
        text="""
        <html><body>
          <h2>Item 7.</h2><p>Management discussion table-of-contents entry.</p>
          <h2>Item 7. Management’s Discussion and Analysis of Financial Condition</h2>
          <p>Management discusses operating results.</p>
        </body></html>
        """,
    )

    chunks = chunk_filing_document(document, max_chars=200, overlap_chars=20)

    assert {chunk.section for chunk in chunks} == {
        "Item 7 — Management's Discussion and Analysis"
    }
