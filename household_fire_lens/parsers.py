from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple


PARSER_VERSION = "2026.07.02.1"


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
    text = re.sub(r"\bNL\d{2}[A-Z0-9]{4}\d{10}\b", " ", text)
    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", " ", text)
    text = re.sub(r"\b(BETAALAUTOMAAT|PASVOLGNR|TRANSACTIE|KENMERK|MACHTIGING|INCASSO)\b", " ", text)
    text = re.sub(r"\b\d{4,}\b", " ", text)
    text = re.sub(r"[^A-Z0-9&.+ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


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
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
        if sample.count(";") > sample.count(","):
            dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ParseError("CSV has no header row")
    original_headers = list(reader.fieldnames)
    normalized_headers = [normalize_header(header or "") for header in original_headers]
    rows: List[Dict[str, str]] = []
    for raw_row in reader:
        row: Dict[str, str] = {}
        for original, normalized in zip(original_headers, normalized_headers):
            row[normalized] = (raw_row.get(original) or "").strip()
        if any(value for value in row.values()):
            rows.append(row)
    return normalized_headers, rows


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
    amount = parse_amount(first_value(row, "bedrag_eur", "bedrag", "amount"), debit_credit)
    description = " ".join(
        part
        for part in [
            first_value(row, "naam_omschrijving", "name_description"),
            first_value(row, "mutatiesoort", "transaction_type"),
            first_value(row, "mededelingen", "description"),
        ]
        if part
    )
    counterparty = first_value(row, "naam_omschrijving", "tegenpartij", "counterparty")
    own_account = first_value(row, "rekening", "account") or account_hint or "ING account"
    counterparty_account = first_value(row, "tegenrekening", "counter_account")
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
    own_account = first_value(row, "rekeningnummer", "account", "account_number") or account_hint or "ABN account"
    counterparty_account = first_value(row, "tegenrekeningnummer", "tegenrekening", "counter_account")
    currency = first_value(row, "valuta", "currency") or "EUR"
    return ParsedTransaction(
        row_number=row_number,
        raw=row,
        institution="abn",
        account_hint=account_hint or own_account,
        account_identifier=own_account,
        transaction_date=date,
        booking_date=date,
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
        counterparty_account=first_value(row, "counter_account", "tegenrekening", "tegenrekeningnummer"),
        description=description or counterparty,
        reference=first_value(row, "reference", "referentie", "id"),
    )
