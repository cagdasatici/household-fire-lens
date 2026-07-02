from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple


PARSER_VERSION = "2026.07.02.4"
IBAN_PATTERN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}")
IBAN_TEXT_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){10,30}\b")
PAYMENT_PROCESSOR_PATTERNS = (
    re.compile(r"^(?P<merchant>.+?)\s+VIA\s+STICHTING\s+MOLLIE\s+PAYMENTS\b"),
    re.compile(r"^(?P<merchant>.+?)\s+VIA\s+MOLLIE\b"),
    re.compile(r"^ZETTLE\s+(?P<merchant>.+)$"),
    re.compile(r"^SUMUP\s+(?P<merchant>.+)$"),
    re.compile(r"^PAY\.NL\s+(?P<merchant>.+)$"),
)
IBAN_LENGTHS = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BR": 29,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IS": 26,
    "IT": 27,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NL": 18,
    "NO": 15,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "SA": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "TN": 24,
    "TR": 26,
    "UA": 29,
}


@dataclass
class ParsedTransaction:
    row_number: int
    raw: Dict[str, str]
    institution: str
    account_hint: str
    account_identifier: str
    transaction_date: str
    booking_date: Optional[str]
    amount: float
    currency: str
    counterparty_name: str
    counterparty_account: str
    description: str
    reference: str


class ParseError(ValueError):
    pass


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def row_hash(raw: Dict[str, str]) -> str:
    joined = "\n".join(f"{key}={raw.get(key, '')}" for key in sorted(raw))
    return stable_hash(joined)


def fingerprint(parts: Iterable[str]) -> str:
    return stable_hash("|".join(part.strip().upper() for part in parts if part is not None))


def normalize_header(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("\ufeff", "")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalize_merchant(value: str) -> str:
    text = (value or "").upper()
    for pattern in PAYMENT_PROCESSOR_PATTERNS:
        match = pattern.search(text)
        if match:
            text = match.group("merchant")
            break
    text = re.split(r"\bPAYMENT TERMINAL\b", text, maxsplit=1)[0]
    text = re.split(r"\bCARD NO\b", text, maxsplit=1)[0]
    text = re.split(r"\bDATE\b", text, maxsplit=1)[0]
    text = re.sub(r"\b(NLD|NL|NETHERLANDS|NEDERLAND)\b$", " ", text.strip())
    text = re.sub(r"\bNL\d{2}[A-Z0-9]{4}\d{10}\b", " ", text)
    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", " ", text)
    text = re.sub(r"\b(BETAALAUTOMAAT|PASVOLGNR|TRANSACTIE|TRANSACTION|KENMERK|MACHTIGING|INCASSO|TERMINAL)\b", " ", text)
    text = re.sub(r"\b\d{4,}\b", " ", text)
    text = re.sub(r"[^A-Z0-9&.+ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def extract_ibans(*values: str) -> List[str]:
    text = " ".join(value or "" for value in values).upper()
    found: List[str] = []
    for match in IBAN_TEXT_PATTERN.finditer(text):
        compact = re.sub(r"[^A-Z0-9]", "", match.group(0))
        country = compact[:2]
        length = IBAN_LENGTHS.get(country)
        if not length:
            continue
        candidate = compact[:length]
        if len(candidate) == length and IBAN_PATTERN.fullmatch(candidate):
            found.append(candidate)
    return list(dict.fromkeys(found))


def extract_iban(*values: str, exclude: Iterable[str] = ()) -> str:
    excluded = {re.sub(r"[^A-Z0-9]", "", value.upper()) for value in exclude if value}
    for iban in extract_ibans(*values):
        if iban not in excluded:
            return iban
    return ""


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def read_csv(content: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    text = decode_csv(content)
    sample = text[:4096]
    delimiter = preferred_delimiter(sample)
    if delimiter:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
            if sample.count(";") > sample.count(","):
                dialect.delimiter = ";"
        reader = csv.reader(io.StringIO(text), dialect=dialect)
    all_rows = [row for row in reader if any((value or "").strip() for value in row)]
    if not all_rows:
        raise ParseError("CSV has no header row")

    first_row = [value.strip() for value in all_rows[0]]
    if looks_like_headerless_abn_tab(first_row):
        original_headers = [
            "account_number",
            "currency",
            "transaction_date",
            "opening_balance",
            "closing_balance",
            "booking_date",
            "amount",
            "description",
        ]
        data_rows = all_rows
    else:
        original_headers = first_row
        data_rows = all_rows[1:]

    normalized_headers = [normalize_header(header or "") for header in original_headers]
    rows: List[Dict[str, str]] = []
    for raw_row in data_rows:
        row: Dict[str, str] = {}
        values = list(raw_row)
        if len(values) > len(normalized_headers):
            values = values[: len(normalized_headers) - 1] + [" ".join(values[len(normalized_headers) - 1 :])]
        for index, normalized in enumerate(normalized_headers):
            row[normalized] = (values[index] if index < len(values) else "").strip()
        if any(value for value in row.values()):
            rows.append(row)
    return normalized_headers, rows


def preferred_delimiter(sample: str) -> str:
    lines = [line for line in sample.splitlines() if line.strip()][:10]
    if not lines:
        return ""
    tab_counts = [line.count("\t") for line in lines]
    semicolon_counts = [line.count(";") for line in lines]
    if min(tab_counts) >= 2:
        return "\t"
    if min(semicolon_counts) >= 2:
        return ";"
    return ""


def looks_like_headerless_abn_tab(row: List[str]) -> bool:
    if len(row) < 8:
        return False
    return (
        bool(re.fullmatch(r"\d{6,18}", row[0].strip()))
        and bool(re.fullmatch(r"[A-Z]{3}", row[1].strip().upper()))
        and bool(re.fullmatch(r"\d{8}", row[2].strip()))
        and bool(re.fullmatch(r"\d{8}", row[5].strip()))
        and bool(re.fullmatch(r"-?\d+(?:[,.]\d+)?", row[6].strip()))
    )


def first_value(row: Dict[str, str], *names: str) -> str:
    for name in names:
        normalized = normalize_header(name)
        value = row.get(normalized)
        if value not in (None, ""):
            return value.strip()
    return ""


def parse_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ParseError("Missing transaction date")
    formats = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%Y%m%d",
        "%d%m%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except ValueError:
            continue
    raise ParseError(f"Could not parse date: {value}")


def parse_amount(value: str, debit_credit: str = "") -> float:
    raw = (value or "").strip()
    if not raw:
        raise ParseError("Missing amount")
    negative = raw.startswith("-") or debit_credit.strip().lower() in {"af", "debit", "d", "uit", "withdrawal"}
    cleaned = raw.replace("EUR", "").replace("€", "").replace(" ", "").replace("+", "").replace("-", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError as exc:
        raise ParseError(f"Could not parse amount: {value}") from exc
    return -abs(amount) if negative else abs(amount)


def detect_institution(filename: str, headers: List[str], requested: Optional[str] = None) -> str:
    if requested:
        return requested.lower()
    name = filename.lower()
    header_set = set(headers)
    if "af_bij" in header_set or "naam_omschrijving" in header_set:
        return "ing"
    if {"account_number", "transaction_date", "booking_date", "amount", "description"} <= header_set:
        return "abn"
    if "tegenrekeningnummer" in header_set or "boekdatum" in header_set:
        return "abn"
    if "clientaccountid" in header_set or "asset_category" in header_set or "ibkr" in name:
        return "ibkr"
    if "degiro" in name or "waarde" in header_set and "isin" in header_set:
        return "degiro"
    if "ing" in name:
        return "ing"
    if "abn" in name:
        return "abn"
    if "degiro" in name:
        return "degiro"
    return "generic"


def parse_transactions(
    filename: str,
    content: bytes,
    institution: Optional[str] = None,
    account_hint: str = "",
) -> Tuple[str, List[ParsedTransaction]]:
    headers, rows = read_csv(content)
    detected = detect_institution(filename, headers, institution)
    parsed: List[ParsedTransaction] = []
    for index, row in enumerate(rows, start=2):
        parsed.append(parse_row(detected, row, index, account_hint))
    return detected, parsed


def parse_row(institution: str, row: Dict[str, str], row_number: int, account_hint: str = "") -> ParsedTransaction:
    if institution == "ing":
        return parse_ing(row, row_number, account_hint)
    if institution == "abn":
        return parse_abn(row, row_number, account_hint)
    if institution == "ibkr":
        return parse_ibkr(row, row_number, account_hint)
    if institution == "degiro":
        return parse_degiro(row, row_number, account_hint)
    return parse_generic(row, row_number, account_hint, institution)


def parse_ing(row: Dict[str, str], row_number: int, account_hint: str) -> ParsedTransaction:
    date = parse_date(first_value(row, "datum", "date", "booking_date"))
    debit_credit = first_value(row, "af_bij", "debit_credit", "credit_debit")
    amount = parse_amount(first_value(row, "bedrag_eur", "amount_eur", "bedrag", "amount"), debit_credit)
    own_account = first_value(row, "rekening", "account") or account_hint or "ING account"
    description = " ".join(
        part
        for part in [
            first_value(row, "naam_omschrijving", "name_description"),
            first_value(row, "mutatiesoort", "transaction_type"),
            first_value(row, "mededelingen", "notifications", "description"),
        ]
        if part
    )
    counterparty = first_value(row, "naam_omschrijving", "tegenpartij", "counterparty")
    counterparty_account = first_value(
        row,
        "tegenrekening",
        "tegenrekeningnummer",
        "counter_account",
        "counterparty_account",
        "counterparty_account_number",
    ) or extract_iban(description, exclude=(own_account,))
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution="ing",
        account_hint=account_hint or own_account,
        account_identifier=own_account,
        transaction_date=date,
        booking_date=date,
        amount=amount,
        currency="EUR",
        counterparty_name=counterparty,
        counterparty_account=counterparty_account,
        description=description,
        reference=first_value(row, "code", "reference"),
    )


def parse_abn(row: Dict[str, str], row_number: int, account_hint: str) -> ParsedTransaction:
    date = parse_date(first_value(row, "boekdatum", "datum", "date", "transaction_date"))
    raw_amount = first_value(row, "bedrag", "amount", "mutatiebedrag")
    debit_credit = first_value(row, "af_bij", "debit_credit")
    amount = parse_amount(raw_amount, debit_credit)
    own_account = first_value(row, "rekeningnummer", "account", "account_number") or account_hint or "ABN account"
    description = " ".join(
        part
        for part in [
            first_value(row, "omschrijving", "description"),
            first_value(row, "transactieomschrijving", "transaction_description"),
            first_value(row, "mededelingen", "remarks"),
        ]
        if part
    )
    counterparty = first_value(row, "naam_tegenpartij", "tegenpartij", "counterparty_name", "counterparty")
    counterparty_account = first_value(
        row,
        "tegenrekeningnummer",
        "tegenrekening",
        "counter_account",
        "counterparty_account",
        "counterparty_account_number",
    ) or extract_iban(description, exclude=(own_account,))
    currency = first_value(row, "valuta", "currency") or "EUR"
    booking_date = first_value(row, "booking_date", "boekdatum", "datum", "date", "transaction_date")
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution="abn",
        account_hint=account_hint or own_account,
        account_identifier=own_account,
        transaction_date=date,
        booking_date=parse_date(booking_date) if booking_date else date,
        amount=amount,
        currency=currency,
        counterparty_name=counterparty,
        counterparty_account=counterparty_account,
        description=description or counterparty,
        reference=first_value(row, "referentie", "reference"),
    )


def parse_ibkr(row: Dict[str, str], row_number: int, account_hint: str) -> ParsedTransaction:
    date = parse_date(first_value(row, "date", "datum", "reportdate", "trade_date"))
    amount = parse_amount(first_value(row, "amount", "cash", "proceeds", "net_amount", "bedrag"))
    description = " ".join(
        part
        for part in [
            first_value(row, "description", "omschrijving", "activity_description"),
            first_value(row, "type", "activity_type"),
            first_value(row, "symbol"),
        ]
        if part
    )
    account = first_value(row, "account", "clientaccountid", "accountid") or account_hint or "IBKR account"
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution="ibkr",
        account_hint=account_hint or account,
        account_identifier=account,
        transaction_date=date,
        booking_date=date,
        amount=amount,
        currency=first_value(row, "currency", "valuta") or "EUR",
        counterparty_name="IBKR",
        counterparty_account="",
        description=description or "IBKR transaction",
        reference=first_value(row, "transactionid", "reference"),
    )


def parse_degiro(row: Dict[str, str], row_number: int, account_hint: str) -> ParsedTransaction:
    date = parse_date(first_value(row, "datum", "date", "boekdatum"))
    amount = parse_amount(first_value(row, "bedrag", "amount", "mutatie", "waarde"))
    description = " ".join(
        part
        for part in [
            first_value(row, "omschrijving", "description"),
            first_value(row, "product", "productnaam"),
            first_value(row, "isin"),
        ]
        if part
    )
    account = first_value(row, "rekening", "account") or account_hint or "DeGiro account"
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution="degiro",
        account_hint=account_hint or account,
        account_identifier=account,
        transaction_date=date,
        booking_date=date,
        amount=amount,
        currency=first_value(row, "valuta", "currency") or "EUR",
        counterparty_name="DeGiro",
        counterparty_account="",
        description=description or "DeGiro transaction",
        reference=first_value(row, "id", "referentie", "reference"),
    )


def parse_generic(row: Dict[str, str], row_number: int, account_hint: str, institution: str) -> ParsedTransaction:
    date = parse_date(first_value(row, "date", "datum", "transaction_date", "booking_date", "boekdatum"))
    amount = parse_amount(first_value(row, "amount", "bedrag", "value", "mutatiebedrag"))
    description = first_value(row, "description", "omschrijving", "name_description", "merchant", "details")
    counterparty = first_value(row, "counterparty", "merchant", "naam_tegenpartij", "name")
    account = first_value(row, "account", "rekening", "rekeningnummer", "account_number") or account_hint or f"{institution} account"
    counterparty_account = first_value(
        row,
        "counter_account",
        "counterparty_account",
        "counterparty_account_number",
        "tegenrekening",
        "tegenrekeningnummer",
    ) or extract_iban(description, exclude=(account,))
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution=institution,
        account_hint=account_hint or account,
        account_identifier=account,
        transaction_date=date,
        booking_date=date,
        amount=amount,
        currency=first_value(row, "currency", "valuta") or "EUR",
        counterparty_name=counterparty,
        counterparty_account=counterparty_account,
        description=description or counterparty,
        reference=first_value(row, "reference", "referentie", "id"),
    )
