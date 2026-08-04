"""Visible-text extraction for SEC inline-XBRL filing documents."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from sec_financial_research.domain.models import FilingChunk, FilingDocument

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_SKIP_TAGS = {"head", "script", "style", "noscript", "svg", "ix:hidden"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_WHITESPACE = re.compile(r"\s+")
_ITEM_HEADING = re.compile(
    r"^item\s+((?:1[0-6]|[1-9])(?:[a-c])?)\s*[.\-–—:]?\s*(.*?)\s*$",
    re.IGNORECASE,
)
_ITEM_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements With Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _VOID_TAGS:
            if normalized_tag in {"br", "hr"} and not self._hidden_depth:
                self.fragments.append("\n")
            return

        hidden = self._hidden_depth > 0 or self._is_hidden(normalized_tag, attrs)
        self._stack.append((normalized_tag, hidden))
        if hidden:
            self._hidden_depth += 1
        elif normalized_tag in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "hr"} and not self._hidden_depth:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        matching_index = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index][0] == normalized_tag
            ),
            None,
        )
        if matching_index is None:
            return
        closing = self._stack[matching_index:]
        was_visible = not closing[0][1]
        self._hidden_depth -= sum(hidden for _, hidden in closing)
        del self._stack[matching_index:]
        if was_visible and normalized_tag in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.fragments.append(data)

    @staticmethod
    def _is_hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _SKIP_TAGS:
            return True
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if "hidden" in attributes or attributes.get("aria-hidden", "").lower() == "true":
            return True
        style = re.sub(r"\s+", "", attributes.get("style", "").lower())
        return "display:none" in style or "visibility:hidden" in style


def extract_filing_text(document_html: str) -> str:
    """Return normalized visible filing text while retaining block boundaries."""

    parser = _VisibleTextParser()
    parser.feed(document_html)
    parser.close()
    lines = []
    for raw_line in "".join(parser.fragments).replace("\u00a0", " ").splitlines():
        line = _WHITESPACE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def chunk_filing_document(
    document: FilingDocument,
    *,
    max_chars: int = 1_800,
    overlap_chars: int = 200,
) -> tuple[FilingChunk, ...]:
    """Split a filing into bounded chunks without crossing detected item sections."""

    if max_chars < 50:
        raise ValueError("max_chars must be at least 50")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than max_chars")

    visible_text = extract_filing_text(document.text)
    chunks: list[FilingChunk] = []
    for section, section_text in _filing_sections(visible_text):
        for chunk_text in _bounded_chunks(
            section_text,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ):
            chunk_index = len(chunks)
            section_slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
            filing = document.filing
            chunks.append(
                FilingChunk(
                    chunk_id=(
                        f"{filing.accession}:{section_slug}:{chunk_index:04d}"
                    ),
                    chunk_index=chunk_index,
                    cik=filing.cik,
                    company_name=filing.company_name,
                    accession=filing.accession,
                    form=filing.form,
                    filing_date=filing.filing_date,
                    report_date=filing.report_date,
                    section=section,
                    text=chunk_text,
                    source_url=document.source_url,
                    index_url=filing.index_url,
                )
            )
    return tuple(chunks)


def _filing_sections(visible_text: str) -> tuple[tuple[str, str], ...]:
    sections: list[tuple[str, str]] = []
    current_section = "Preamble"
    current_lines: list[str] = []
    for line in visible_text.splitlines():
        match = _ITEM_HEADING.fullmatch(line)
        if match:
            if current_lines:
                sections.append((current_section, " ".join(current_lines)))
            item_number = match.group(1).upper()
            detected_title = match.group(2).strip(" .:-–—")
            title = _ITEM_TITLES.get(item_number) or detected_title or "Untitled"
            current_section = f"Item {item_number} — {title}"
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_section, " ".join(current_lines)))
    return tuple(sections)


def _bounded_chunks(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[str, ...]:
    words = [
        piece
        for word in text.split()
        for piece in (
            [word]
            if len(word) <= max_chars
            else [word[index : index + max_chars] for index in range(0, len(word), max_chars)]
        )
    ]
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start
        length = 0
        while end < len(words):
            candidate_length = length + (1 if length else 0) + len(words[end])
            if candidate_length > max_chars:
                break
            length = candidate_length
            end += 1
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        overlap_start = end
        overlap_length = 0
        while overlap_start > start:
            word_length = len(words[overlap_start - 1])
            candidate_length = overlap_length + (1 if overlap_length else 0) + word_length
            if candidate_length > overlap_chars:
                break
            overlap_start -= 1
            overlap_length = candidate_length
        start = end if overlap_start == start else overlap_start
    return tuple(chunks)
