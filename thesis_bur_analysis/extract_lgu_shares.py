#!/usr/bin/env python3
"""Extract LGU IRA/NTA/budget rows from DBM PDFs.

The DBM memoranda in this folder do not always keep table columns aligned the
same way across years. This extractor avoids hard-coded x-coordinates and
instead:

1. groups PDF words into lines,
2. treats a trailing run of amount-looking tokens as the numeric columns, and
3. merges wrapped LGU names that spill onto the next line.

Outputs:
    - one CSV per PDF
    - one combined CSV for all PDFs
    - one JSON summary with row counts and parser notes
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import pdfplumber  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dependency availability is runtime-specific
    pdfplumber = None

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dependency availability is runtime-specific
    try:
        from PyPDF2 import PdfReader  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - dependency availability is runtime-specific
        PdfReader = None


AMOUNT_TOKEN_RE = re.compile(
    r"""
    ^
    [\(\[]?
    -?
    (?:
        \d{1,3}(?:,\d{3})+(?:\.\d+)?
        |
        \d+\.\d+
        |
        \d{4,}
    )
    %?
    [\)\]]?
    [*]?
    $
    """,
    re.VERBOSE,
)
YEAR_RE = re.compile(r"(19|20)\d{2}")
CY_PERIOD_RE = re.compile(r"\bCY\s*(20\d{2})\b", re.IGNORECASE)
FY_PERIOD_RE = re.compile(r"\bFY\s*(20\d{2})\b", re.IGNORECASE)
NUMBERED_LABEL_RE = re.compile(r"^\d+\.\s*")
MULTI_NUMBERED_LABEL_RE = re.compile(r"\b\d+\.\s*[A-Za-z]")
REGION_TEXT_RE = re.compile(
    r"\b(?:NATIONAL CAPITAL REGION|REGION\s+[IVXLC]+(?:\.[A-Z])?|CARAGA|CORDILLERA ADMINISTRATIVE REGION|BANGSAMORO AUTONOMOUS REGION IN MUSLIM MINDANAO)\b",
    re.IGNORECASE,
)
REGIONAL_OFFICE_RE = re.compile(
    r"^REGIONAL OFFICE NO\.?\s*([IVXLC]+|\d+)(?:\s+INC\.?\s+ARMM)?(?:\s*\((CARAGA)\))?\b",
    re.IGNORECASE,
)
PROVINCE_TEXT_RE = re.compile(r"^(?:PROVINCE:\s*|Province of\s+)(.+)$", re.IGNORECASE)
GROUP_HEADING_RE = re.compile(
    r"^(?:CITY|CITIES|BARANGAY|BARANGAYS|MUNICIPALIT(?:Y|IES)|MUNICI\w+|MUNICIPALITIES/CITIES|MUNICIPALITIES\s*/\s*CITIES|MUNICIPALITIES/CITY|MUNICIPALITIES/CITIES)$",
    re.IGNORECASE,
)
CITY_PREFIX_RE = re.compile(r"^(?:CITY\s+|City of\s+)", re.IGNORECASE)
MUNICIPALITY_PREFIX_RE = re.compile(r"^(?:MUNICIPALITY\s+OF\s+|Municipality of\s+)", re.IGNORECASE)
BARANGAY_PREFIX_RE = re.compile(r"^(?:BARANGAY\s+|Barangay\s+|BRGY\.?\s+)", re.IGNORECASE)
PROVINCE_PREFIX_RE = re.compile(r"^(?:Province of\s+)", re.IGNORECASE)
NON_PLACE_CHARS_RE = re.compile(r"[^A-Za-z0-9 .'\-/&()]")
REGION_ROW_PREFIX_RE = re.compile(
    r"^(NATIONAL CAPITAL REGION|CORDILLERA ADMINISTRATIVE REGION|NEGROS ISLAND REGION|BANGSAMORO AUTONOMOUS REGION IN MUSLIM MINDANAO|CARAGA|REGION\s+[IVXLC]+(?:\.[A-Z])?|REGIONAL OFFICE NO\.?\s*(?:[IVXLC]+|\d+)(?:\s+INC\.?\s+ARMM)?(?:\s*\(CARAGA\))?)\b",
    re.IGNORECASE,
)
HEADER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^republic of the philippines$",
        r"^department of budget and management$",
        r"^department of budget\s*&\s*management$",
        r"^local budget memorandum.*$",
        r"^lbm\b.*$",
        r"^annex\b.*$",
        r"^for guidelines.*$",
        r"^guidelines and procedures.*$",
        r"^official.*$",
        r"^fiscal year\b.*$",
        r"^fy\s+\d{4}.*$",
        r"^share(?:s)?$",
        r"^allocation(?:s)?$",
        r"^amount(?:s)?$",
        r"^page\s+\d+(\s+of\s+\d+)?$",
        r"^cy\s+\d{4}.*$",
        r"^region provinces cities municipalities barangays grand total$",
        r"^province(?:s)?\s*/\s*cit(?:y|ies)\s*/\s*municipalit(?:y|ies).*$",
        r"^province(?:s)?\s*/\s*cit(?:y|ies)\s*/\s*municipalit(?:y|ies)\s*/\s*barangay(?:s)?.*$",
        r"^provinces?$",
        r"^cities?$",
        r"^municipalit(?:y|ies)$",
        r"^barangay(?:s)?$",
        r"^first\s+semester$",
        r"^second\s+semester$",
        r"^first\s+quarter$",
        r"^second\s+quarter$",
        r"^third\s+quarter$",
        r"^fourth\s+quarter$",
    ]
]
IGNORE_CLEAN_LABEL_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^ira$",
        r"^nta$",
        r"^as of\b.*$",
        r"^population\b.*$",
        r"^no\.$",
        r"^under ra\b.*$",
        r"^ra no\..*$",
        r"^part\s+\d+.*$",
        r"^sample\b.*$",
        r"^general solano\b.*$",
        r"^shares of local government units\b.*$",
        r"^local government units\b.*$",
        r"^official\b.*$",
        r"^barangays shall\b.*$",
        r"^other mope\b.*$",
        r"^region provinces cities municipalities barangays grand total$",
        r"^-+$",
        r"^_+$",
        r"^grand total\b.*$",
        r"^total\b.*$",
        r"^subtotal\b.*$",
    ]
]


@dataclass(frozen=True)
class Token:
    text: str
    x0: Optional[float] = None
    x1: Optional[float] = None


@dataclass(frozen=True)
class Line:
    page_number: int
    line_number: int
    text: str
    tokens: Tuple[Token, ...]
    top: Optional[float] = None
    bottom: Optional[float] = None


@dataclass(frozen=True)
class ParsedRow:
    source_file: str
    source_stem: str
    document_year: str
    page_number: int
    line_number: int
    section_path: str
    row_type: str
    label: str
    amount_count: int
    raw_text: str
    amounts: Tuple[str, ...]


@dataclass(frozen=True)
class CleanRow:
    source_file: str
    source_stem: str
    document_year: str
    document_category: str
    layout_family: str
    region: str
    province: str
    lgu_type: str
    lgu_name: str
    lgu_sort_name: str
    page_number: int
    line_number: int
    section_path: str
    source_label: str
    amount_count: int
    raw_text: str
    amounts: Tuple[str, ...]


@dataclass(frozen=True)
class RegionalSummaryRow:
    source_file: str
    source_stem: str
    document_category: str
    share_type: str
    period_type: str
    period_year: str
    period_label: str
    layout_family: str
    region: str
    provinces: str
    cities: str
    municipalities: str
    province_city_municipal_total: str
    barangays: str
    grand_total: str
    page_number: int
    line_number: int
    raw_text: str
    is_imputed: str
    imputed_from_period: str
    imputation_note: str


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Extract LGU IRA/NTA/budget rows from all PDFs in a folder."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=script_dir,
        help="Folder containing the DBM PDFs. Defaults to this script's folder.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=script_dir / "extracted_lgu_rows",
        help="Folder where CSV and JSON outputs will be written.",
    )
    parser.add_argument(
        "--pattern",
        default="*.pdf",
        help="Glob pattern used to find PDFs inside the input directory.",
    )
    parser.add_argument(
        "--line-y-tolerance",
        type=float,
        default=3.0,
        help="Vertical tolerance for grouping PDF words into lines.",
    )
    parser.add_argument(
        "--min-table-lines",
        type=int,
        default=4,
        help="Minimum data-like lines before a page is treated as a table page.",
    )
    return parser.parse_args()


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def strip_numbering_prefix(text: str) -> str:
    return normalize_space(NUMBERED_LABEL_RE.sub("", text, count=1))


def extract_region_name(text: str) -> str:
    normalized = normalize_space(text)
    match = REGION_TEXT_RE.search(normalized)
    return normalize_space(match.group(0)) if match else ""


def extract_province_name(text: str) -> str:
    normalized = normalize_space(text)
    match = PROVINCE_TEXT_RE.match(normalized)
    if not match:
        return ""
    candidate = normalize_space(match.group(1).strip(" .,:;"))
    return candidate if looks_like_place_name(candidate) else ""


def normalize_group_heading(text: str) -> str:
    normalized = normalize_space(text).upper()
    if not GROUP_HEADING_RE.fullmatch(normalized):
        return ""
    if "BARANGAY" in normalized:
        return "BARANGAYS"
    if "CITY" in normalized and "MUNIC" not in normalized:
        return "CITIES" if normalized.endswith("IES") else "CITY"
    return "MUNICIPALITIES/CITIES" if "/" in normalized else "MUNICIPALITIES"


def normalize_sort_text(text: str) -> str:
    normalized = normalize_space(text)
    normalized = PROVINCE_PREFIX_RE.sub("", normalized)
    normalized = CITY_PREFIX_RE.sub("", normalized)
    normalized = MUNICIPALITY_PREFIX_RE.sub("", normalized)
    normalized = BARANGAY_PREFIX_RE.sub("", normalized)
    normalized = NON_PLACE_CHARS_RE.sub(" ", normalized)
    return normalize_space(normalized).casefold()


def safe_output_stem(pdf_path: Path, max_length: int = 72) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_path.stem).strip("._")
    stem = re.sub(r"_+", "_", stem)
    if not stem:
        stem = "document"
    digest = hashlib.md5(pdf_path.name.encode("utf-8")).hexdigest()[:8]
    if len(stem) > max_length:
        stem = stem[: max_length - 9].rstrip("._")
    return f"{stem}_{digest}"


ROMAN_REGION_NUMBERS = {
    "1": "I",
    "2": "II",
    "3": "III",
    "4": "IV",
    "5": "V",
    "6": "VI",
    "7": "VII",
    "8": "VIII",
    "9": "IX",
    "10": "X",
    "11": "XI",
    "12": "XII",
    "13": "XIII",
}


def normalize_ocr_amount_token(token: str) -> str:
    working = normalize_space(token).strip("[](){};:")
    if not working:
        return ""
    suffix = "%" if working.endswith("%") else ""
    if suffix:
        working = working[:-1]
    working = working.replace("O", "0")
    separators = [char for char in working if char in ",."]
    if not separators:
        return f"{working}{suffix}"

    last_comma = working.rfind(",")
    last_dot = working.rfind(".")
    last_sep_index = max(last_comma, last_dot)
    digits_after_last = len(re.sub(r"\D", "", working[last_sep_index + 1 :]))

    if digits_after_last == 2:
        integer_part = re.sub(r"[,.]", "", working[:last_sep_index])
        decimal_part = re.sub(r"\D", "", working[last_sep_index + 1 :])
        if integer_part and decimal_part:
            return f"{integer_part}.{decimal_part}{suffix}"

    digits_only = re.sub(r"[,.]", "", working)
    return f"{digits_only}{suffix}"


def extract_amount_tokens(text: str) -> List[str]:
    tokens: List[str] = []
    for part in normalize_space(text).split():
        candidate = normalize_ocr_amount_token(part)
        if candidate and is_amount_token(candidate):
            tokens.append(candidate)
    return tokens


def normalize_region_name(text: str) -> str:
    normalized = normalize_space(text)
    if not normalized:
        return ""

    regional_office_match = REGIONAL_OFFICE_RE.match(normalized)
    if regional_office_match:
        office = regional_office_match.group(1).upper()
        office = ROMAN_REGION_NUMBERS.get(office, office)
        return f"REGION {office}"

    region_match = re.match(r"^REGION\s+([IVXLC]+(?:\.[A-Z])?)\b", normalized, re.IGNORECASE)
    if region_match:
        return f"REGION {region_match.group(1).upper()}"

    region = extract_region_name(normalized)
    return region.upper() if region else normalized.upper()


def looks_like_place_name(text: str) -> bool:
    normalized = normalize_space(text)
    if not normalized:
        return False
    if len(normalized.split()) > 7:
        return False
    if any(char.isdigit() for char in normalized):
        return False
    if "%" in normalized or "/" in normalized:
        return False
    if "  " in normalized:
        return False
    lower = normalized.casefold()
    blocked_terms = {
        "ira",
        "nta",
        "official",
        "grand total",
        "total",
        "subtotal",
        "city-funded hospitals",
        "municipalities/cities",
        "municipalities",
        "cities",
        "city",
        "barangays",
        "province",
    }
    if lower in blocked_terms:
        return False
    if NON_PLACE_CHARS_RE.search(normalized):
        return False
    return any(char.isalpha() for char in normalized)


def sanitize_token(text: str) -> str:
    cleaned = normalize_space(text)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip("*")
    cleaned = cleaned.replace("\u20b1", "").replace("PHP", "").replace("P", "")
    return cleaned


def is_amount_token(text: str) -> bool:
    cleaned = sanitize_token(text)
    return bool(AMOUNT_TOKEN_RE.fullmatch(cleaned))


def normalize_amount(text: str) -> str:
    cleaned = sanitize_token(text)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()[]").replace(",", "").rstrip("%")
    if negative:
        cleaned = f"-{cleaned}"
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return cleaned
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def is_noise_line(text: str) -> bool:
    normalized = normalize_space(text)
    if not normalized:
        return True
    if normalized.isdigit():
        return True
    return any(pattern.match(normalized) for pattern in HEADER_PATTERNS)


def looks_like_context(text: str) -> bool:
    normalized = normalize_space(text)
    if not normalized or is_noise_line(normalized):
        return False
    if split_trailing_amount_text(normalized) is not None:
        return False
    if extract_region_name(normalized):
        return True
    if extract_province_name(normalized):
        return True
    return bool(normalize_group_heading(normalized))


def looks_like_fragment(text: str) -> bool:
    normalized = normalize_space(text)
    if not normalized or is_noise_line(normalized) or looks_like_context(normalized):
        return False
    if len(normalized.split()) > 12:
        return False
    if normalized.endswith("."):
        return False
    return any(char.isalpha() for char in normalized)


def looks_like_wrapped_label_prefix(text: str) -> bool:
    normalized = normalize_space(text)
    if not normalized or is_noise_line(normalized):
        return False
    if normalized.endswith((":",";",".")):
        return False
    lower = normalized.lower()
    if lower.startswith("region "):
        return False
    tokens = lower.split()
    if len(tokens) > 8:
        return False
    trailing_connectors = {
        "of",
        "de",
        "del",
        "san",
        "santa",
        "santo",
        "sto",
        "sto.",
        "sta",
        "sta.",
        "norte",
        "sur",
        "city",
        "municipality",
        "province",
    }
    return tokens[-1] in trailing_connectors or normalized.endswith("-")


def join_tokens(tokens: Sequence[Token]) -> str:
    text = " ".join(token.text for token in tokens if token.text.strip())
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return normalize_space(text)


def split_trailing_amount_tokens(
    tokens: Sequence[Token],
) -> Optional[Tuple[str, Tuple[str, ...]]]:
    if not tokens:
        return None

    index = len(tokens)
    amounts: List[str] = []
    while index > 0 and is_amount_token(tokens[index - 1].text):
        amounts.append(tokens[index - 1].text)
        index -= 1

    if not amounts or index == 0:
        return None

    if any(is_amount_token(token.text) for token in tokens[:index]):
        return None

    label = join_tokens(tokens[:index])
    if not label or is_noise_line(label):
        return None

    normalized_amounts = tuple(normalize_amount(value) for value in reversed(amounts))
    return label, normalized_amounts


def split_trailing_amount_text(text: str) -> Optional[Tuple[str, Tuple[str, ...]]]:
    normalized = normalize_space(text)
    if not normalized:
        return None

    parts = normalized.split()
    tokens = tuple(Token(part) for part in parts)
    return split_trailing_amount_tokens(tokens)


def group_pdfplumber_lines(pdf_path: Path, y_tolerance: float) -> List[List[Line]]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed")

    pages: List[List[Line]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            working_page = page.dedupe_chars() if hasattr(page, "dedupe_chars") else page
            words = working_page.extract_words(
                x_tolerance=1,
                y_tolerance=y_tolerance,
                use_text_flow=True,
                keep_blank_chars=False,
            )

            if not words:
                pages.append([])
                continue

            sorted_words = sorted(words, key=lambda word: (word["top"], word["x0"]))
            lines: List[List[Dict[str, object]]] = []
            current_line: List[Dict[str, object]] = [sorted_words[0]]
            current_tops = [float(sorted_words[0]["top"])]

            for word in sorted_words[1:]:
                top = float(word["top"])
                reference_top = median(current_tops)
                if abs(top - reference_top) <= y_tolerance:
                    current_line.append(word)
                    current_tops.append(top)
                else:
                    lines.append(current_line)
                    current_line = [word]
                    current_tops = [top]

            lines.append(current_line)

            page_lines: List[Line] = []
            for line_number, group in enumerate(lines, start=1):
                ordered = sorted(group, key=lambda word: word["x0"])
                tokens = tuple(
                    Token(
                        text=normalize_space(str(word["text"])),
                        x0=float(word["x0"]),
                        x1=float(word["x1"]),
                    )
                    for word in ordered
                )
                text = join_tokens(tokens)
                if not text:
                    continue
                page_lines.append(
                    Line(
                        page_number=page_number,
                        line_number=line_number,
                        text=text,
                        tokens=tokens,
                        top=min(float(word["top"]) for word in ordered),
                        bottom=max(float(word["bottom"]) for word in ordered),
                    )
                )
            pages.append(page_lines)
    return pages


def group_pypdf_lines(pdf_path: Path) -> List[List[Line]]:
    if PdfReader is None:
        raise RuntimeError("pypdf/PyPDF2 is not installed")

    reader = PdfReader(str(pdf_path))
    pages: List[List[Line]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        lines: List[Line] = []
        for line_number, raw_line in enumerate(page_text.splitlines(), start=1):
            text = normalize_space(raw_line)
            if not text:
                continue
            tokens = tuple(Token(part) for part in text.split())
            lines.append(
                Line(
                    page_number=page_number,
                    line_number=line_number,
                    text=text,
                    tokens=tokens,
                )
            )
        pages.append(lines)
    return pages


def load_lines(pdf_path: Path, y_tolerance: float) -> Tuple[List[List[Line]], str]:
    errors: List[str] = []

    if pdfplumber is not None:
        try:
            return group_pdfplumber_lines(pdf_path, y_tolerance), "pdfplumber"
        except Exception as exc:  # pragma: no cover - depends on external PDFs
            errors.append(f"pdfplumber failed: {exc}")

    if PdfReader is not None:
        try:
            return group_pypdf_lines(pdf_path), "pypdf"
        except Exception as exc:  # pragma: no cover - depends on external PDFs
            errors.append(f"pypdf failed: {exc}")

    detail = "; ".join(errors) if errors else "no supported PDF library found"
    raise RuntimeError(f"Unable to extract text from {pdf_path.name}: {detail}")


def line_has_data_shape(line: Line) -> bool:
    if is_noise_line(line.text):
        return False
    split = split_trailing_amount_tokens(line.tokens)
    if split is None:
        return False
    label, amounts = split
    if len(amounts) == 0:
        return False
    if len(label.split()) > 18 and label.endswith("."):
        return False
    return True


def repeated_header_lines(pages: Sequence[Sequence[Line]]) -> Set[str]:
    counter: Counter = Counter()
    for page in pages:
        page_seen: Set[str] = set()
        for line in page:
            normalized = normalize_space(line.text).lower()
            if not normalized or len(normalized) < 5:
                continue
            if line.top is not None and line.top > 120:
                continue
            page_seen.add(normalized)
        counter.update(page_seen)

    repeated: Set[str] = set()
    threshold = 2
    for text, count in counter.items():
        if count >= threshold and any(pattern.match(text) for pattern in HEADER_PATTERNS):
            repeated.add(text)
    return repeated


def is_table_page(lines: Sequence[Line], min_table_lines: int) -> bool:
    data_like = sum(1 for line in lines if line_has_data_shape(line))
    amount_tokens = 0
    for line in lines:
        split = split_trailing_amount_tokens(line.tokens)
        if split is not None:
            amount_tokens += len(split[1])

    page_text = " ".join(line.text.lower() for line in lines[:18])
    has_keywords = any(
        keyword in page_text
        for keyword in (
            "province",
            "city",
            "municipality",
            "barangay",
            "share",
            "allocation",
            "ira",
            "nta",
        )
    )
    return data_like >= min_table_lines or amount_tokens >= (min_table_lines * 2) or (
        has_keywords and data_like >= 2
    )


def infer_document_year(pdf_path: Path) -> str:
    match = YEAR_RE.search(pdf_path.stem)
    return match.group(0) if match else ""


def update_context(existing: List[str], text: str) -> List[str]:
    clean = normalize_space(text)
    region = extract_region_name(clean)
    province = extract_province_name(clean)
    group = normalize_group_heading(clean)

    if region:
        return [region]

    if province:
        if existing and extract_region_name(existing[0]):
            return [existing[0], f"PROVINCE: {province}"]
        return [f"PROVINCE: {province}"]

    if group:
        base = list(existing[:2])
        if base and base[-1] == group:
            return base
        return [*base, group]

    return existing


def classify_row_type(label: str) -> str:
    lower = label.lower()
    if lower.startswith("grand total") or lower.startswith("total") or lower.startswith("subtotal"):
        return "total"
    return "data"


def non_noise_line_after(
    lines: Sequence[Line], start_index: int, repeated_headers: Set[str]
) -> Optional[Line]:
    for line in lines[start_index + 1 :]:
        normalized = normalize_space(line.text).lower()
        if not normalized or normalized in repeated_headers or is_noise_line(normalized):
            continue
        return line
    return None


def parse_pdf(
    pdf_path: Path,
    y_tolerance: float,
    min_table_lines: int,
) -> Tuple[List[ParsedRow], Dict[str, object]]:
    pages, engine = load_lines(pdf_path, y_tolerance)
    table_pages = [page for page in pages if is_table_page(page, min_table_lines)]
    repeated_headers = repeated_header_lines(table_pages)

    rows: List[ParsedRow] = []
    context: List[str] = []
    parser_notes: List[str] = []
    document_year = infer_document_year(pdf_path)

    for page in table_pages:
        pending_fragments: List[str] = []
        for index, line in enumerate(page):
            normalized = normalize_space(line.text)
            normalized_lower = normalized.lower()
            if not normalized or normalized_lower in repeated_headers or is_noise_line(normalized):
                continue

            split = split_trailing_amount_tokens(line.tokens)
            if split is not None:
                label, amounts = split
                full_label = normalize_space(" ".join([*pending_fragments, label]))
                pending_fragments = []
                section_path = " > ".join(context)
                rows.append(
                    ParsedRow(
                        source_file=pdf_path.name,
                        source_stem=pdf_path.stem,
                        document_year=document_year,
                        page_number=line.page_number,
                        line_number=line.line_number,
                        section_path=section_path,
                        row_type=classify_row_type(full_label),
                        label=full_label,
                        amount_count=len(amounts),
                        raw_text=normalized,
                        amounts=amounts,
                    )
                )
                continue

            next_line = non_noise_line_after(page, index, repeated_headers)
            if (
                next_line
                and split_trailing_amount_tokens(next_line.tokens) is not None
                and looks_like_wrapped_label_prefix(normalized)
            ):
                pending_fragments.append(normalized)
                continue

            if (
                next_line
                and split_trailing_amount_tokens(next_line.tokens) is not None
                and looks_like_fragment(normalized)
            ):
                pending_fragments.append(normalized)
                continue

            if looks_like_context(normalized):
                context = update_context(context, normalized)
                pending_fragments = []

    if not rows:
        parser_notes.append("No rows were extracted. This PDF may need OCR or tighter header filters.")

    summary = {
        "source_file": pdf_path.name,
        "engine": engine,
        "document_year": document_year,
        "pages_total": len(pages),
        "pages_used_as_tables": len(table_pages),
        "raw_rows_extracted": len(rows),
        "max_amount_columns": max((row.amount_count for row in rows), default=0),
        "notes": parser_notes,
    }
    return rows, summary


def extract_context_metadata(section_path: str) -> Tuple[str, str, str]:
    region = ""
    province = ""
    group = ""
    for part in [normalize_space(piece) for piece in section_path.split(">") if normalize_space(piece)]:
        if not region:
            region = extract_region_name(part)
        province_candidate = extract_province_name(part)
        if province_candidate:
            province = province_candidate
        group_candidate = normalize_group_heading(part)
        if group_candidate:
            group = group_candidate
    return region, province, group


def clean_place_label(label: str, lgu_type: str) -> str:
    cleaned = strip_numbering_prefix(label)
    if lgu_type == "province":
        cleaned = PROVINCE_PREFIX_RE.sub("", cleaned)
    elif lgu_type == "city":
        cleaned = CITY_PREFIX_RE.sub("", cleaned)
    elif lgu_type == "municipality":
        cleaned = MUNICIPALITY_PREFIX_RE.sub("", cleaned)
    elif lgu_type == "barangay":
        cleaned = BARANGAY_PREFIX_RE.sub("", cleaned)
    return normalize_space(cleaned.strip(" .,:;"))


def detect_lgu_type(
    row: ParsedRow, region: str, province: str, group: str
) -> Tuple[str, str]:
    label = normalize_space(row.label)
    if not label or row.row_type != "data":
        return "", ""

    stripped_label = strip_numbering_prefix(label)
    lower_label = stripped_label.casefold()

    if label.casefold() == "provincial share" and province:
        return "province", province

    explicit_region = extract_region_name(stripped_label)
    if explicit_region and row.amount_count >= 2:
        return "region", explicit_region

    explicit_province = extract_province_name(stripped_label)
    if explicit_province:
        return "province", explicit_province

    if lower_label.startswith("city-funded"):
        return "", ""

    if CITY_PREFIX_RE.match(stripped_label):
        return "city", clean_place_label(stripped_label, "city")

    if MUNICIPALITY_PREFIX_RE.match(stripped_label):
        return "municipality", clean_place_label(stripped_label, "municipality")

    if BARANGAY_PREFIX_RE.match(stripped_label):
        return "barangay", clean_place_label(stripped_label, "barangay")

    if province and looks_like_place_name(stripped_label):
        if group in {"CITY", "CITIES"}:
            return "city", stripped_label
        if group in {"MUNICIPALITIES", "MUNICIPALITIES/CITIES"}:
            inferred_type = "city" if stripped_label.casefold().startswith("city of ") else "municipality"
            return inferred_type, clean_place_label(stripped_label, inferred_type)
        return "municipality", stripped_label

    if region and looks_like_place_name(stripped_label) and row.amount_count >= 2:
        return "province", stripped_label

    return "", ""


def should_keep_clean_row(
    row: ParsedRow, region: str, province: str, lgu_type: str, lgu_name: str
) -> bool:
    label = normalize_space(row.label)
    raw_text = normalize_space(row.raw_text)

    if not lgu_type or not lgu_name:
        return False

    if len(MULTI_NUMBERED_LABEL_RE.findall(label)) > 1:
        return False

    if any(pattern.match(label) for pattern in IGNORE_CLEAN_LABEL_PATTERNS):
        if not (label.casefold() == "provincial share" and province):
            return False

    if len(strip_numbering_prefix(label).split()) > 9 and lgu_type != "region":
        return False

    if lgu_type != "region" and not looks_like_place_name(lgu_name):
        return False

    if raw_text.casefold().startswith("ira ") or raw_text.casefold().startswith("nta "):
        return False

    if region == "" and province == "" and lgu_type not in {"region", "province"}:
        return False

    return True


def build_clean_row(row: ParsedRow) -> Optional[CleanRow]:
    region, province, group = extract_context_metadata(row.section_path)
    lgu_type, lgu_name = detect_lgu_type(row, region, province, group)
    lgu_name = normalize_space(lgu_name)

    if not should_keep_clean_row(row, region, province, lgu_type, lgu_name):
        return None

    province_value = province
    region_value = region
    if lgu_type == "region" and not region_value:
        region_value = lgu_name
    if lgu_type == "province" and not province_value:
        province_value = lgu_name

    return CleanRow(
        source_file=row.source_file,
        source_stem=row.source_stem,
        document_year=row.document_year,
        document_category="",
        layout_family="",
        region=region_value,
        province=province_value,
        lgu_type=lgu_type,
        lgu_name=lgu_name,
        lgu_sort_name=normalize_sort_text(lgu_name),
        page_number=row.page_number,
        line_number=row.line_number,
        section_path=row.section_path,
        source_label=row.label,
        amount_count=row.amount_count,
        raw_text=row.raw_text,
        amounts=row.amounts,
    )


def clean_row_sort_key(row: CleanRow) -> Tuple[str, str, int, str, str, str, int, int]:
    type_rank = {
        "region": 0,
        "province": 1,
        "city": 2,
        "municipality": 3,
        "barangay": 4,
    }.get(row.lgu_type, 99)
    return (
        normalize_sort_text(row.region),
        normalize_sort_text(row.province),
        type_rank,
        row.lgu_sort_name,
        row.document_year,
        row.source_file.casefold(),
        row.page_number,
        row.line_number,
    )


def build_clean_rows(
    rows: Sequence[ParsedRow], document_category: str, layout_family: str
) -> List[CleanRow]:
    clean_rows = []
    for clean_row in (build_clean_row(row) for row in rows):
        if not clean_row:
            continue
        clean_rows.append(
            CleanRow(
                source_file=clean_row.source_file,
                source_stem=clean_row.source_stem,
                document_year=clean_row.document_year,
                document_category=document_category,
                layout_family=layout_family,
                region=clean_row.region,
                province=clean_row.province,
                lgu_type=clean_row.lgu_type,
                lgu_name=clean_row.lgu_name,
                lgu_sort_name=clean_row.lgu_sort_name,
                page_number=clean_row.page_number,
                line_number=clean_row.line_number,
                section_path=clean_row.section_path,
                source_label=clean_row.source_label,
                amount_count=clean_row.amount_count,
                raw_text=clean_row.raw_text,
                amounts=clean_row.amounts,
            )
        )
    return sorted(clean_rows, key=clean_row_sort_key)


def document_probe_text(pdf_path: Path, rows: Sequence[ParsedRow], limit: int = 40) -> str:
    pieces = [pdf_path.name, pdf_path.stem]
    for row in rows[:limit]:
        pieces.extend([row.section_path, row.label, row.raw_text])
    return normalize_space(" ".join(piece for piece in pieces if piece)).casefold()


def has_any_term(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def classify_document(pdf_path: Path, rows: Sequence[ParsedRow]) -> Dict[str, object]:
    name = pdf_path.name.casefold()
    probe = document_probe_text(pdf_path, rows)

    legacy_ira_names = {"cy2008.pdf", "cy2009.pdf", "cy2010.pdf", "cy2014.pdf", "ira.pdf"}
    ira_summary_name_hints = [
        "lbm63",
        "lbm no.68",
        "lbmno70-a_adjusted fy 2016 ira",
        "lbm_no.74-a",
        "local budget memorandum no. 75-a",
        "lbm no. 65",
        "local budget memorandum no. 67-a",
        "local budget memorandum no. 77",
        "local-budget-memorandum-no-77-b",
        "local-budget-memorandum-no.-78-a",
        "local-budget-memorandum-no-80-a",
        "local-budget-memorandum-no-82-a",
        "local-budget-memorandum-no. 85-b",
    ]
    nta_summary_name_hints = [
        "local-budget-memorandum-no-87-a",
        "local-budget-memorandum-no.-90a",
        "local-budget-memorandum-no.-92-dated-june-9,-2025",
        "local-budget-memorandum-no-92b",
    ]
    excise_name_hints = [
        "lbmno.69_",
        "lbm_no72",
        "lbm_no73",
        "local budget memorandum no. 76",
        "local budget memorandum no. 76-a",
        "local-budget-memorandum-no-79",
        "local-budget-memorandum-no-81",
        "local-budget-memorandum-no-83",
        "local-budget-memorandum-no-86",
        "local-budget-memorandum-no-89",
        "local-budget-memorandum-no-91",
        "local-budget-memorandum-no.-93",
    ]

    if name in legacy_ira_names:
        return {
            "document_category": "ira_detail",
            "layout_family": "legacy_detail_7col",
            "include_in_clean_output": True,
            "category_reason": "Matched the CY legacy IRA detail file set.",
        }

    if has_any_term(name, excise_name_hints) or has_any_term(
        probe,
        [
            "excise",
            "virginia-type",
            "virginia - type",
            "burley",
            "native tobacco",
            "cigarette",
            "production share",
            "ra no. 7171",
            "ra no. 8240",
            "ra no. 10351",
            "ra no. 11346",
        ],
    ):
        return {
            "document_category": "excise_tax_share",
            "layout_family": "excise_distribution",
            "include_in_clean_output": False,
            "category_reason": "Detected excise-tax share terms such as RA 7171/8240/10351/11346, cigarettes, or tobacco.",
        }

    if has_any_term(name, nta_summary_name_hints) or has_any_term(
        probe,
        [
            "national tax allotment",
            "total nta shares",
            "nta shares",
            "fy 2026 nta",
            "shares of local government units (lgus)",
            "region provinces cities municipalities barangays grand total",
        ],
    ):
        return {
            "document_category": "nta_summary",
            "layout_family": "regional_summary",
            "include_in_clean_output": True,
            "category_reason": "Detected NTA summary wording or the region/province/city/municipality/barangay grand-total layout.",
        }

    if has_any_term(name, ira_summary_name_hints) or has_any_term(
        probe,
        [
            "internal revenue allotment",
            "total ira shares",
            "ira p",
            "level of lgu",
            "functions/city-funded",
            "city-funded hospitals",
            "appropriation 2008",
            "appropriation 2009",
            "appropriation 2010",
            "appropriation 2014",
        ],
    ):
        return {
            "document_category": "ira_summary",
            "layout_family": "regional_summary",
            "include_in_clean_output": True,
            "category_reason": "Detected IRA wording or the by-level/by-region IRA summary layout.",
        }

    return {
        "document_category": "review_needed",
        "layout_family": "unknown",
        "include_in_clean_output": False,
        "category_reason": "Could not classify confidently from the filename or extracted text. Review manually.",
    }


def estimate_text_layer_quality(rows: Sequence[ParsedRow]) -> str:
    if not rows:
        return "none"
    sample = document_probe_text(Path(rows[0].source_file), rows, limit=25)
    if "cid:" in sample or "official^" in sample or "�" in sample:
        return "poor"
    return "ok"


def build_pages_probe_text(pages: Sequence[Sequence[Line]], line_limit: int = 160) -> str:
    pieces: List[str] = []
    for page in pages:
        for line in page:
            pieces.append(line.text)
            if len(pieces) >= line_limit:
                return normalize_space(" ".join(pieces)).casefold()
    return normalize_space(" ".join(pieces)).casefold()


def infer_share_type(pdf_path: Path, pages: Sequence[Sequence[Line]]) -> str:
    probe = " ".join([pdf_path.name, pdf_path.stem, build_pages_probe_text(pages)])
    lowered = normalize_space(probe).casefold()
    if "nta" in lowered or "national tax allotment" in lowered:
        return "NTA"
    if "ira" in lowered or "internal revenue allotment" in lowered:
        return "IRA"
    return ""


def infer_period_info(
    pdf_path: Path, pages: Sequence[Sequence[Line]], share_type: str
) -> Tuple[str, str, str, str]:
    probe = " ".join([pdf_path.name, pdf_path.stem, build_pages_probe_text(pages)])

    if pdf_path.name.casefold() == "lbm63.pdf":
        return "FY", "2011", "FY 2011", "manual override from LBM63 filename"

    cy_match = CY_PERIOD_RE.search(probe)
    if cy_match:
        year = cy_match.group(1)
        return "CY", year, f"CY {year}", "detected in PDF text"

    fy_match = FY_PERIOD_RE.search(probe)
    if fy_match:
        year = fy_match.group(1)
        return "FY", year, f"FY {year}", "detected in PDF text"

    filename_match = re.search(r"\b(20\d{2})\b", pdf_path.name)
    if filename_match:
        year = filename_match.group(1)
        fallback_period = "FY" if share_type == "NTA" else "CY"
        return fallback_period, year, f"{fallback_period} {year}", "fallback from filename year"

    stem_match = re.search(r"cy(20\d{2})", pdf_path.stem, re.IGNORECASE)
    if stem_match:
        year = stem_match.group(1)
        return "CY", year, f"CY {year}", "fallback from filename stem"

    return "", "", "", ""


def match_region_prefix(text: str) -> Tuple[str, str]:
    normalized = normalize_space(text)
    match = REGION_ROW_PREFIX_RE.match(normalized)
    if not match:
        return "", ""
    prefix = normalize_space(match.group(1))
    region = normalize_region_name(prefix)
    remainder = normalize_space(normalized[match.end() :])
    return region, remainder


def parse_regional_summary_candidate(
    text: str, share_type: str
) -> Optional[Tuple[str, Dict[str, str]]]:
    region, remainder = match_region_prefix(text)
    if not region:
        return None

    amount_tokens = extract_amount_tokens(remainder)
    amount_count = len(amount_tokens)
    if amount_count not in {5, 6}:
        return None

    normalized_amounts = [normalize_amount(token) for token in amount_tokens]
    summary = {
        "provinces": "",
        "cities": "",
        "municipalities": "",
        "province_city_municipal_total": "",
        "barangays": "",
        "grand_total": "",
    }

    if amount_count == 5:
        summary["provinces"] = normalized_amounts[0]
        summary["cities"] = normalized_amounts[1]
        summary["municipalities"] = normalized_amounts[2]
        summary["barangays"] = normalized_amounts[3]
        summary["grand_total"] = normalized_amounts[4]
    else:
        summary["provinces"] = normalized_amounts[0]
        summary["cities"] = normalized_amounts[1]
        summary["municipalities"] = normalized_amounts[2]
        summary["province_city_municipal_total"] = normalized_amounts[3]
        summary["barangays"] = normalized_amounts[4]
        summary["grand_total"] = normalized_amounts[5]

    if share_type == "NTA" and amount_count == 6:
        return None

    if share_type == "IRA" and amount_count == 5:
        grand = Decimal(summary["grand_total"])
        subtotal = sum(
            Decimal(summary[key]) for key in ("provinces", "cities", "municipalities", "barangays")
        )
        if grand != subtotal:
            return None

    return region, summary


def combine_summary_candidate_text(lines: Sequence[Line], index: int) -> List[str]:
    candidates = [lines[index].text]
    if index + 1 < len(lines):
        next_text = normalize_space(lines[index + 1].text)
        if next_text and not match_region_prefix(next_text)[0]:
            candidates.append(normalize_space(f"{lines[index].text} {next_text}"))
    return candidates


def extract_regional_summary_rows(
    pdf_path: Path, y_tolerance: float, document_category: str, layout_family: str
) -> Tuple[List[RegionalSummaryRow], Dict[str, object]]:
    pages, engine = load_lines(pdf_path, y_tolerance)
    share_type = infer_share_type(pdf_path, pages)
    period_type, period_year, period_label, period_source = infer_period_info(pdf_path, pages, share_type)

    rows: List[RegionalSummaryRow] = []
    seen: Set[Tuple[str, str, str, str, str, str, str, str, str, str, str]] = set()

    for page in pages:
        for index, line in enumerate(page):
            for candidate_text in combine_summary_candidate_text(page, index):
                parsed = parse_regional_summary_candidate(candidate_text, share_type)
                if not parsed:
                    continue
                region, amounts = parsed
                dedupe_key = (
                    pdf_path.name,
                    share_type,
                    period_type,
                    period_year,
                    region,
                    amounts["provinces"],
                    amounts["cities"],
                    amounts["municipalities"],
                    amounts["province_city_municipal_total"],
                    amounts["barangays"],
                    amounts["grand_total"],
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    RegionalSummaryRow(
                        source_file=pdf_path.name,
                        source_stem=pdf_path.stem,
                        document_category=document_category,
                        share_type=share_type,
                        period_type=period_type,
                        period_year=period_year,
                        period_label=period_label,
                        layout_family=layout_family,
                        region=region,
                        provinces=amounts["provinces"],
                        cities=amounts["cities"],
                        municipalities=amounts["municipalities"],
                        province_city_municipal_total=amounts["province_city_municipal_total"],
                        barangays=amounts["barangays"],
                        grand_total=amounts["grand_total"],
                        page_number=line.page_number,
                        line_number=line.line_number,
                        raw_text=normalize_space(candidate_text),
                        is_imputed="",
                        imputed_from_period="",
                        imputation_note="",
                    )
                )
                break

    metadata = {
        "summary_engine": engine,
        "share_type": share_type,
        "period_type": period_type,
        "period_year": period_year,
        "period_label": period_label,
        "period_inference": period_source,
        "regional_summary_rows": len(rows),
    }
    return rows, metadata


def regional_summary_sort_key(row: RegionalSummaryRow) -> Tuple[str, str, str, str]:
    return (
        row.period_type,
        row.period_year,
        normalize_sort_text(row.region),
        row.source_file.casefold(),
    )


def apply_cy2011_ira_fill_rule(
    rows: Sequence[RegionalSummaryRow], pdf_paths: Sequence[Path]
) -> List[RegionalSummaryRow]:
    if any(
        row.share_type == "IRA" and row.period_type == "CY" and row.period_year == "2011"
        for row in rows
    ):
        return sorted(list(rows), key=regional_summary_sort_key)

    if not any(path.name.casefold() == "lbm63.pdf" for path in pdf_paths):
        return sorted(list(rows), key=regional_summary_sort_key)

    fy2011_rows = [
        row
        for row in rows
        if row.share_type == "IRA" and row.period_type == "FY" and row.period_year == "2011"
    ]
    cy2010_rows = [
        row
        for row in rows
        if row.share_type == "IRA" and row.period_type == "CY" and row.period_year == "2010"
    ]
    seed_rows = fy2011_rows or cy2010_rows
    if not seed_rows:
        return sorted(list(rows), key=regional_summary_sort_key)

    note = "Filled CY 2011 IRA from LBM63 guidance that FY 2011 IRA shares are the same as CY 2010."
    source_period = "FY 2011" if fy2011_rows else "CY 2010"
    source_file = "LBM63.pdf" if fy2011_rows else seed_rows[0].source_file

    result = list(rows)
    for row in seed_rows:
        result.append(
            RegionalSummaryRow(
                source_file=source_file,
                source_stem=Path(source_file).stem,
                document_category="ira_summary",
                share_type="IRA",
                period_type="CY",
                period_year="2011",
                period_label="CY 2011",
                layout_family=row.layout_family,
                region=row.region,
                provinces=row.provinces,
                cities=row.cities,
                municipalities=row.municipalities,
                province_city_municipal_total=row.province_city_municipal_total,
                barangays=row.barangays,
                grand_total=row.grand_total,
                page_number=row.page_number,
                line_number=row.line_number,
                raw_text=row.raw_text,
                is_imputed="yes",
                imputed_from_period=source_period,
                imputation_note=note,
            )
        )

    return sorted(result, key=regional_summary_sort_key)


def raw_csv_fieldnames(rows: Sequence[ParsedRow]) -> List[str]:
    max_amounts = max((row.amount_count for row in rows), default=0)
    base = [
        "source_file",
        "source_stem",
        "document_year",
        "page_number",
        "line_number",
        "section_path",
        "row_type",
        "label",
        "amount_count",
        "raw_text",
    ]
    amount_fields = [f"amount_{index}" for index in range(1, max_amounts + 1)]
    return [*base, *amount_fields]


def raw_row_to_record(row: ParsedRow, max_amounts: int) -> Dict[str, str]:
    record = {
        "source_file": row.source_file,
        "source_stem": row.source_stem,
        "document_year": row.document_year,
        "page_number": str(row.page_number),
        "line_number": str(row.line_number),
        "section_path": row.section_path,
        "row_type": row.row_type,
        "label": row.label,
        "amount_count": str(row.amount_count),
        "raw_text": row.raw_text,
    }
    for index in range(max_amounts):
        record[f"amount_{index + 1}"] = row.amounts[index] if index < len(row.amounts) else ""
    return record


def write_rows_csv(csv_path: Path, rows: Sequence[ParsedRow]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = raw_csv_fieldnames(rows)
    max_amounts = max((row.amount_count for row in rows), default=0)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(raw_row_to_record(row, max_amounts))


def clean_csv_fieldnames(rows: Sequence[CleanRow]) -> List[str]:
    max_amounts = max((row.amount_count for row in rows), default=0)
    base = [
        "source_file",
        "source_stem",
        "document_year",
        "document_category",
        "layout_family",
        "region",
        "province",
        "lgu_type",
        "lgu_name",
        "page_number",
        "line_number",
        "section_path",
        "source_label",
        "amount_count",
        "raw_text",
    ]
    amount_fields = [f"amount_{index}" for index in range(1, max_amounts + 1)]
    return [*base, *amount_fields]


def clean_row_to_record(row: CleanRow, max_amounts: int) -> Dict[str, str]:
    record = {
        "source_file": row.source_file,
        "source_stem": row.source_stem,
        "document_year": row.document_year,
        "document_category": row.document_category,
        "layout_family": row.layout_family,
        "region": row.region,
        "province": row.province,
        "lgu_type": row.lgu_type,
        "lgu_name": row.lgu_name,
        "page_number": str(row.page_number),
        "line_number": str(row.line_number),
        "section_path": row.section_path,
        "source_label": row.source_label,
        "amount_count": str(row.amount_count),
        "raw_text": row.raw_text,
    }
    for index in range(max_amounts):
        record[f"amount_{index + 1}"] = row.amounts[index] if index < len(row.amounts) else ""
    return record


def write_clean_rows_csv(csv_path: Path, rows: Sequence[CleanRow]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = clean_csv_fieldnames(rows)
    max_amounts = max((row.amount_count for row in rows), default=0)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(clean_row_to_record(row, max_amounts))


def regional_summary_fieldnames() -> List[str]:
    return [
        "source_file",
        "source_stem",
        "document_category",
        "share_type",
        "period_type",
        "period_year",
        "period_label",
        "layout_family",
        "region",
        "provinces",
        "cities",
        "municipalities",
        "province_city_municipal_total",
        "barangays",
        "grand_total",
        "page_number",
        "line_number",
        "raw_text",
        "is_imputed",
        "imputed_from_period",
        "imputation_note",
    ]


def regional_summary_row_to_record(row: RegionalSummaryRow) -> Dict[str, str]:
    return {
        "source_file": row.source_file,
        "source_stem": row.source_stem,
        "document_category": row.document_category,
        "share_type": row.share_type,
        "period_type": row.period_type,
        "period_year": row.period_year,
        "period_label": row.period_label,
        "layout_family": row.layout_family,
        "region": row.region,
        "provinces": row.provinces,
        "cities": row.cities,
        "municipalities": row.municipalities,
        "province_city_municipal_total": row.province_city_municipal_total,
        "barangays": row.barangays,
        "grand_total": row.grand_total,
        "page_number": str(row.page_number),
        "line_number": str(row.line_number),
        "raw_text": row.raw_text,
        "is_imputed": row.is_imputed,
        "imputed_from_period": row.imputed_from_period,
        "imputation_note": row.imputation_note,
    }


def write_regional_summary_csv(csv_path: Path, rows: Sequence[RegionalSummaryRow]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=regional_summary_fieldnames())
        writer.writeheader()
        for row in rows:
            writer.writerow(regional_summary_row_to_record(row))


def write_summary_json(json_path: Path, summary: Dict[str, object]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)


def write_inventory_csv(csv_path: Path, rows: Sequence[Dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_file",
        "document_year",
        "document_category",
        "share_type",
        "period_type",
        "period_year",
        "period_label",
        "layout_family",
        "include_in_clean_output",
        "text_layer_quality",
        "likely_needs_ocr_or_manual_review",
        "pages_total",
        "pages_used_as_tables",
        "raw_rows_extracted",
        "clean_rows_written",
        "regional_summary_rows",
        "period_inference",
        "category_reason",
        "notes",
        "output_regional_summary_csv",
        "output_csv",
        "output_raw_csv",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            notes = record.get("notes", [])
            if isinstance(notes, list):
                record["notes"] = " | ".join(str(note) for note in notes)
            writer.writerow({key: record.get(key, "") for key in fieldnames})


def find_pdfs(input_dir: Path, pattern: str) -> Iterable[Path]:
    return sorted(path for path in input_dir.glob(pattern) if path.is_file())


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    pdf_paths = list(find_pdfs(input_dir, args.pattern))

    if not pdf_paths:
        print(f"No PDFs matched {args.pattern!r} inside {input_dir}", file=sys.stderr)
        return 1

    all_rows: List[ParsedRow] = []
    all_clean_rows: List[CleanRow] = []
    all_summary_rows: List[RegionalSummaryRow] = []
    file_summaries: List[Dict[str, object]] = []

    for pdf_path in pdf_paths:
        try:
            rows, summary = parse_pdf(
                pdf_path=pdf_path,
                y_tolerance=args.line_y_tolerance,
                min_table_lines=args.min_table_lines,
            )
        except Exception as exc:  # pragma: no cover - depends on external PDFs
            file_summaries.append(
                {
                    "source_file": pdf_path.name,
                    "engine": "",
                    "document_year": infer_document_year(pdf_path),
                    "document_category": "review_needed",
                    "share_type": "",
                    "period_type": "",
                    "period_year": "",
                    "period_label": "",
                    "period_inference": "",
                    "layout_family": "unknown",
                    "include_in_clean_output": False,
                    "category_reason": "The PDF could not be parsed. Review manually.",
                    "text_layer_quality": "none",
                    "likely_needs_ocr_or_manual_review": True,
                    "pages_total": 0,
                    "pages_used_as_tables": 0,
                    "raw_rows_extracted": 0,
                    "clean_rows_written": 0,
                    "regional_summary_rows": 0,
                    "max_amount_columns": 0,
                    "notes": [str(exc)],
                    "output_regional_summary_csv": "",
                    "output_csv": "",
                    "output_raw_csv": "",
                }
            )
            continue

        all_rows.extend(rows)
        doc_info = classify_document(pdf_path, rows)
        summary.update(doc_info)
        summary["text_layer_quality"] = estimate_text_layer_quality(rows)
        summary["likely_needs_ocr_or_manual_review"] = bool(
            summary["text_layer_quality"] != "ok" or summary["raw_rows_extracted"] == 0
        )
        summary_rows, summary_meta = extract_regional_summary_rows(
            pdf_path=pdf_path,
            y_tolerance=args.line_y_tolerance,
            document_category=str(summary["document_category"]),
            layout_family=str(summary["layout_family"]),
        )
        summary.update(summary_meta)
        all_summary_rows.extend(summary_rows)

        clean_rows = (
            build_clean_rows(
                rows,
                document_category=str(summary["document_category"]),
                layout_family=str(summary["layout_family"]),
            )
            if bool(summary["include_in_clean_output"])
            else []
        )
        all_clean_rows.extend(clean_rows)

        output_stem = safe_output_stem(pdf_path)
        per_file_summary_name = f"{output_stem}_regional_summary.csv"
        per_file_clean_name = f"{output_stem}_rows.csv"
        per_file_raw_name = f"{output_stem}_raw_rows.csv"
        write_regional_summary_csv(output_dir / per_file_summary_name, sorted(summary_rows, key=regional_summary_sort_key))
        write_clean_rows_csv(output_dir / per_file_clean_name, clean_rows)
        write_rows_csv(output_dir / per_file_raw_name, rows)

        summary["clean_rows_written"] = len(clean_rows)
        summary["output_regional_summary_csv"] = per_file_summary_name
        summary["output_csv"] = per_file_clean_name
        summary["output_raw_csv"] = per_file_raw_name
        if not bool(summary["include_in_clean_output"]):
            summary.setdefault("notes", [])
            summary["notes"] = list(summary["notes"]) + [
                f"Excluded from combined_rows.csv because it was classified as {summary['document_category']}."
            ]
        if rows and not clean_rows:
            summary.setdefault("notes", [])
            summary["notes"] = list(summary["notes"]) + [
                "Raw rows were found but none passed the LGU cleanup filters."
            ]
        file_summaries.append(summary)

    all_summary_rows = apply_cy2011_ira_fill_rule(all_summary_rows, pdf_paths)
    write_clean_rows_csv(output_dir / "combined_rows.csv", sorted(all_clean_rows, key=clean_row_sort_key))
    write_rows_csv(output_dir / "combined_rows_raw.csv", all_rows)
    write_regional_summary_csv(
        output_dir / "regional_summary_combined.csv",
        sorted(all_summary_rows, key=regional_summary_sort_key),
    )
    write_inventory_csv(output_dir / "document_inventory.csv", file_summaries)
    write_summary_json(
        output_dir / "extraction_summary.json",
        {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "files_processed": len(pdf_paths),
            "raw_rows_extracted_total": len(all_rows),
            "clean_rows_written_total": len(all_clean_rows),
            "regional_summary_rows_total": len(all_summary_rows),
            "files": file_summaries,
        },
    )

    print(f"Processed {len(pdf_paths)} PDF(s)")
    print(f"Regional summary written to: {output_dir / 'regional_summary_combined.csv'}")
    print(f"Combined rows written to: {output_dir / 'combined_rows.csv'}")
    print(f"Combined raw rows written to: {output_dir / 'combined_rows_raw.csv'}")
    print(f"Document inventory written to: {output_dir / 'document_inventory.csv'}")
    print(f"Summary written to: {output_dir / 'extraction_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
