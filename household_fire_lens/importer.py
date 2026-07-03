from __future__ import annotations

import csv
import io
import sqlite3
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .database import json_dumps
from .parsers import (
    PARSER_VERSION,
    ParseError,
    ParsedTransaction,
    file_sha256,
    fingerprint,
    normalize_merchant,
    parse_transactions,
    row_hash,
    stable_hash,
)


def infer_statement_year(parsed_transactions) -> Optional[int]:
    years = [int(tx.transaction_date[:4]) for tx in parsed_transactions if tx.transaction_date]
    return max(set(years), key=years.count) if years else None


def default_role_for_institution(institution: str, requested_role: Optional[str]) -> str:
    if requested_role:
        return requested_role
    if institution in {"ibkr", "degiro"}:
        return "investment"
    if institution == "wise":
        return "wise"
    if institution == "amex":
        return "credit_card"
    return "unknown"


def import_csv(
    conn: sqlite3.Connection,
    filename: str,
    content: bytes,
    institution: Optional[str] = None,
    account_role: Optional[str] = None,
    account_hint: str = "",
) -> Dict[str, Any]:
    file_hash = file_sha256(content)
    existing = conn.execute("SELECT id, status FROM source_files WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing and existing["status"] == "imported":
        return {
            "status": "duplicate_file",
            "source_file_id": existing["id"],
            "filename": filename,
            "imported": 0,
            "duplicates": 0,
            "message": "This exact file was already imported.",
        }
    if existing:
        conn.execute("DELETE FROM source_files WHERE id = ?", (existing["id"],))
        conn.commit()

    detected, parsed = parse_transactions(filename, content, institution, account_hint)
    statement_year = infer_statement_year(parsed)
    cursor = conn.execute(
        """
        INSERT INTO source_files (
            institution, account_hint, filename, file_hash, statement_year, parser_version, row_count, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'imported')
        """,
        (detected, account_hint, filename, file_hash, statement_year, PARSER_VERSION, len(parsed)),
    )
    source_file_id = cursor.lastrowid
    imported = 0
    duplicates = 0

    for tx in parsed:
        raw_hash = row_hash(tx.raw)
        raw_cursor = conn.execute(
            """
            INSERT OR IGNORE INTO raw_transactions (source_file_id, row_number, raw_json, row_hash)
            VALUES (?, ?, ?, ?)
            """,
            (source_file_id, tx.row_number, json_dumps(tx.raw), raw_hash),
        )
        raw_id = raw_cursor.lastrowid
        if not raw_id:
            raw_id = conn.execute(
                "SELECT id FROM raw_transactions WHERE source_file_id = ? AND row_hash = ?",
                (source_file_id, raw_hash),
            ).fetchone()["id"]

        account_hash = stable_hash(tx.account_identifier or tx.account_hint or f"{detected}:unknown")
        role = default_role_for_institution(detected, account_role)
        account_row = conn.execute(
            "SELECT id, role FROM accounts WHERE institution = ? AND account_identifier_hash = ?",
            (detected, account_hash),
        ).fetchone()
        if account_row:
            account_id = account_row["id"]
            if account_role and account_row["role"] in {"unknown", ""}:
                conn.execute("UPDATE accounts SET role = ? WHERE id = ?", (account_role, account_id))
        else:
            account_cursor = conn.execute(
                """
                INSERT INTO accounts (institution, display_name, role, currency, account_identifier_hash, is_own_account)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (detected, tx.account_hint or f"{detected.upper()} account", role, tx.currency, account_hash),
            )
            account_id = account_cursor.lastrowid

        converted_amount, converted_currency, amount_detail = normalize_amount_for_storage(conn, tx)
        merchant = normalize_merchant(tx.counterparty_name) or normalize_merchant(tx.description)
        direction = "inflow" if converted_amount >= 0 else "outflow"
        counterparty_account_hash = None
        if tx.counterparty_account:
            candidate_hash = stable_hash(tx.counterparty_account)
            if candidate_hash != account_hash:
                counterparty_account_hash = candidate_hash
        source_fingerprint = fingerprint(
            [
                str(account_id),
                tx.transaction_date,
                f"{converted_amount:.2f}",
                converted_currency,
                tx.counterparty_name,
                tx.description,
                tx.reference,
            ]
        )
        duplicate_row = conn.execute(
            """
            SELECT id FROM normalized_transactions
            WHERE source_fingerprint = ? AND is_duplicate = 0
            LIMIT 1
            """,
            (source_fingerprint,),
        ).fetchone()
        is_duplicate = 1 if duplicate_row else 0
        if is_duplicate:
            duplicates += 1

        normalized_cursor = conn.execute(
            """
            INSERT INTO normalized_transactions (
                raw_transaction_id, source_file_id, account_id, transaction_date, booking_date,
                amount, currency, direction, counterparty_name, counterparty_account_hash,
                description, normalized_merchant, reference, source_fingerprint, is_duplicate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id,
                source_file_id,
                account_id,
                tx.transaction_date,
                tx.booking_date,
                converted_amount,
                converted_currency,
                direction,
                tx.counterparty_name,
                counterparty_account_hash,
                tx.description,
                merchant,
                tx.reference,
                source_fingerprint,
                is_duplicate,
            ),
        )
        normalized_id = normalized_cursor.lastrowid
        store_amount_detail(conn, normalized_id, amount_detail)
        store_balance_observations(conn, account_id, source_file_id, normalized_id, tx)
        imported += 1
        if duplicate_row:
            conn.execute(
                """
                INSERT OR IGNORE INTO transaction_links (
                    link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
                ) VALUES ('duplicate', ?, ?, ?, 1.0, 'Same account/date/amount/counterparty/reference fingerprint')
                """,
                (normalized_id, duplicate_row["id"], abs(converted_amount)),
            )

    conn.commit()
    return {
        "status": "imported",
        "source_file_id": source_file_id,
        "filename": filename,
        "institution": detected,
        "statement_year": statement_year,
        "imported": imported,
        "duplicates": duplicates,
        "parser_version": PARSER_VERSION,
    }


def normalize_amount_for_storage(conn: sqlite3.Connection, tx: ParsedTransaction) -> Tuple[float, str, Dict[str, Any]]:
    native_amount = tx.native_amount if tx.native_amount is not None else tx.amount
    native_currency = (tx.native_currency or tx.currency or "EUR").upper()
    amount = float(tx.amount)
    currency = (tx.currency or "EUR").upper()
    detail = {
        "native_amount": native_amount,
        "native_currency": native_currency,
        "source_amount": tx.source_amount,
        "source_currency": tx.source_currency,
        "target_amount": tx.target_amount,
        "target_currency": tx.target_currency,
        "exchange_rate": tx.exchange_rate,
        "converted_amount": amount,
        "converted_currency": currency,
    }
    if currency == "EUR":
        stored_amount = round(amount, 2)
        detail["converted_amount"] = stored_amount
        return stored_amount, currency, detail

    sign = -1.0 if amount < 0 else 1.0
    converted = None
    source_currency = (tx.source_currency or "").upper()
    target_currency = (tx.target_currency or "").upper()
    if source_currency == "EUR" and tx.source_amount is not None:
        converted = abs(float(tx.source_amount))
    elif target_currency == "EUR" and tx.target_amount is not None:
        converted = abs(float(tx.target_amount))
    else:
        converted = abs(amount) * lookup_eur_per_unit(conn, tx.transaction_date, currency)
    converted_amount = round(sign * converted, 2)
    detail["converted_amount"] = converted_amount
    detail["converted_currency"] = "EUR"
    return converted_amount, "EUR", detail


def store_amount_detail(conn: sqlite3.Connection, transaction_id: int, detail: Dict[str, Any]) -> None:
    if not detail.get("native_currency") or detail.get("native_currency") == detail.get("converted_currency"):
        if detail.get("source_currency") in (None, "", detail.get("converted_currency")):
            return
    conn.execute(
        """
        INSERT OR REPLACE INTO transaction_amount_details (
            transaction_id, native_amount, native_currency, source_amount, source_currency,
            target_amount, target_currency, exchange_rate, converted_amount, converted_currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            detail.get("native_amount"),
            detail.get("native_currency"),
            detail.get("source_amount"),
            detail.get("source_currency"),
            detail.get("target_amount"),
            detail.get("target_currency"),
            detail.get("exchange_rate"),
            detail.get("converted_amount"),
            detail.get("converted_currency") or "EUR",
        ),
    )


def store_balance_observations(
    conn: sqlite3.Connection,
    account_id: int,
    source_file_id: int,
    transaction_id: int,
    tx: ParsedTransaction,
) -> None:
    observations = [
        ("opening", tx.opening_balance),
        ("closing", tx.closing_balance),
        ("resulting", tx.resulting_balance),
    ]
    for balance_type, amount in observations:
        if amount is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO balance_observations (
                account_id, source_file_id, transaction_id, observation_date,
                balance_type, amount, currency, confidence, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.98, ?)
            """,
            (
                account_id,
                source_file_id,
                transaction_id,
                tx.transaction_date,
                balance_type,
                amount,
                tx.currency or "EUR",
                f"{balance_type} balance captured from source row",
            ),
        )


def lookup_eur_per_unit(conn: sqlite3.Connection, date: str, currency: str) -> float:
    currency = currency.upper()
    if currency == "EUR":
        return 1.0
    row = conn.execute(
        """
        SELECT eur_per_unit
        FROM fx_rates
        WHERE currency = ? AND rate_date <= ?
        ORDER BY rate_date DESC
        LIMIT 1
        """,
        (currency, date),
    ).fetchone()
    if not row:
        ensure_ecb_rates(conn, [currency])
        row = conn.execute(
            """
            SELECT eur_per_unit
            FROM fx_rates
            WHERE currency = ? AND rate_date <= ?
            ORDER BY rate_date DESC
            LIMIT 1
            """,
            (currency, date),
        ).fetchone()
    if not row:
        raise ParseError(f"Missing ECB FX rate for {currency} on or before {date}")
    return float(row["eur_per_unit"])


def ensure_ecb_rates(conn: sqlite3.Connection, currencies: Iterable[str]) -> None:
    currencies = {currency.upper() for currency in currencies if currency and currency.upper() != "EUR"}
    missing = [
        currency
        for currency in currencies
        if not conn.execute("SELECT 1 FROM fx_rates WHERE currency = ? LIMIT 1", (currency,)).fetchone()
    ]
    if not missing:
        return
    url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
        text = archive.read(csv_name).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        rate_date = row.get("Date") or row.get("date")
        if not rate_date:
            continue
        for currency in missing:
            raw_rate = (row.get(currency) or "").strip()
            if not raw_rate:
                continue
            rate = float(raw_rate)
            conn.execute(
                """
                INSERT OR IGNORE INTO fx_rates (rate_date, currency, eur_per_unit, source)
                VALUES (?, ?, ?, 'ecb_hist')
                """,
                (rate_date, currency, 1.0 / rate),
            )
    conn.commit()


def record_failed_import(
    conn: sqlite3.Connection,
    filename: str,
    content: bytes,
    institution: str,
    account_hint: str,
    status: str,
    error: str,
) -> Dict[str, Any]:
    file_hash = file_sha256(content)
    existing = conn.execute("SELECT id, status FROM source_files WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing and existing["status"] == "imported":
        return {
            "status": "duplicate_file",
            "source_file_id": existing["id"],
            "filename": filename,
            "imported": 0,
            "duplicates": 0,
            "message": "This exact file was already imported.",
        }
    conn.execute(
        """
        INSERT OR REPLACE INTO source_files (
            id, institution, account_hint, filename, file_hash, statement_year,
            parser_version, row_count, status, error_message
        ) VALUES (
            COALESCE((SELECT id FROM source_files WHERE file_hash = ?), NULL),
            ?, ?, ?, ?, NULL, ?, 0, ?, ?
        )
        """,
        (file_hash, institution, account_hint, filename, file_hash, PARSER_VERSION, status, error[:1000]),
    )
    conn.commit()
    return {
        "status": status,
        "filename": filename,
        "institution": institution,
        "imported": 0,
        "duplicates": 0,
        "error": error,
    }


def hints_for_path(path: Path) -> Tuple[Optional[str], Optional[str], str]:
    parts = {part.lower() for part in path.parts}
    if "ing_savings" in parts:
        return "ing", "savings", "ING Savings"
    if "ing_checking" in parts:
        return "ing", "checking", "ING Checking"
    if "abn_checking_and_others" in parts:
        return "abn", "checking", "ABN Checking"
    if "wise" in parts:
        return "wise", "wise", "Wise"
    if "degiro" in parts:
        return "degiro", "investment", "DeGiro"
    if "ibkr" in parts:
        return "ibkr", "investment", "IBKR"
    if "amex_cc" in parts:
        return "amex", "credit_card", "Amex"
    if "ing_cc" in parts:
        return "ing_credit_card", "credit_card", "ING Credit Card"
    return None, None, ""


def import_directory(conn: sqlite3.Connection, directory: str) -> Dict[str, Any]:
    root = Path(directory)
    results: List[Dict[str, Any]] = []
    supported = {".csv", ".tsv", ".tab"}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name.startswith("."):
            continue
        institution, role, account_hint = hints_for_path(path.relative_to(root))
        filename = str(path.relative_to(root))
        content = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix not in supported:
            result = record_failed_import(
                conn,
                filename,
                content,
                institution or "unknown",
                account_hint,
                "unsupported",
                f"Unsupported file extension for R2.5 importer: {path.suffix}",
            )
            results.append(result)
            continue
        try:
            result = import_csv(conn, filename, content, institution, role, account_hint)
        except Exception as exc:
            result = record_failed_import(
                conn,
                filename,
                content,
                institution or "unknown",
                account_hint,
                "failed",
                str(exc),
            )
        results.append(result)
    status_counts: Dict[str, int] = {}
    imported_rows = 0
    duplicates = 0
    for result in results:
        status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1
        imported_rows += int(result.get("imported") or 0)
        duplicates += int(result.get("duplicates") or 0)
    return {
        "directory": str(root),
        "files": len(results),
        "status_counts": status_counts,
        "imported_rows": imported_rows,
        "duplicates": duplicates,
        "results": results,
    }
