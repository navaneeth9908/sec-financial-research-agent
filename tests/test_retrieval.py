from sec_financial_research.ai.retrieval import HybridFilingRetriever
from sec_financial_research.domain.models import FilingChunk


def _chunk(*, chunk_id: str, chunk_index: int, section: str, text: str) -> FilingChunk:
    return FilingChunk(
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        cik="0000320193",
        company_name="Apple Inc.",
        accession="0000320193-25-000079",
        form="10-K",
        filing_date="2025-10-31",
        report_date="2025-09-27",
        section=section,
        text=text,
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/aapl-20250927.htm"
        ),
        index_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/0000320193-25-000079-index.html"
        ),
    )


def test_lexical_search_is_stable_and_preserves_sec_citation_metadata():
    chunks = (
        _chunk(
            chunk_id="business",
            chunk_index=0,
            section="Item 1 — Business",
            text="The Company designs and sells consumer devices and services.",
        ),
        _chunk(
            chunk_id="supply-chain-risk",
            chunk_index=1,
            section="Item 1A — Risk Factors",
            text=(
                "Component shortages and global supply chain constraints could "
                "delay manufacturing and product availability."
            ),
        ),
        _chunk(
            chunk_id="cybersecurity",
            chunk_index=2,
            section="Item 1C — Cybersecurity",
            text="Cybersecurity threats could disrupt information systems.",
        ),
    )
    retriever = HybridFilingRetriever(chunks)

    first = retriever.search(
        "component shortages in the supply chain",
        limit=2,
        mode="lexical",
    )
    second = retriever.search(
        "component shortages in the supply chain",
        limit=2,
        mode="lexical",
    )

    assert first == second
    assert [result.rank for result in first] == [1]
    assert first[0].chunk.chunk_id == "supply-chain-risk"
    assert first[0].score == first[0].lexical_score > 0
    assert first[0].matched_terms == ("chain", "component", "shortages", "supply")
    assert first[0].chunk.accession == "0000320193-25-000079"
    assert first[0].chunk.source_url.endswith("aapl-20250927.htm")
    assert first[0].chunk.index_url.endswith("0000320193-25-000079-index.html")


def test_hybrid_search_boosts_exact_sec_section_matches_over_body_mentions():
    chunks = (
        _chunk(
            chunk_id="business-mentions-risk",
            chunk_index=0,
            section="Item 1 — Business",
            text="This overview links readers to risk factors and other disclosures.",
        ),
        _chunk(
            chunk_id="risk-factors",
            chunk_index=1,
            section="Item 1A — Risk Factors",
            text=(
                "Competition and adverse macroeconomic conditions could materially "
                "affect operating results."
            ),
        ),
    )
    retriever = HybridFilingRetriever(chunks)

    lexical = retriever.search("risk factors", limit=1, mode="lexical")
    hybrid = retriever.search("risk factors", limit=1, mode="hybrid")

    assert lexical[0].chunk.chunk_id == "business-mentions-risk"
    assert hybrid[0].chunk.chunk_id == "risk-factors"
    assert hybrid[0].matched_terms == ("factors", "risk")
    assert hybrid[0].lexical_score == 0
