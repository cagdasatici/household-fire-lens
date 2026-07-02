from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from .database import json_dumps
from .parsers import (
    PARSER_VERSION,
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
    existing = conn.execute("SELECT id FROM source_files WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing:
        return {
            "status": "duplicate_file",
            "source_file_id": existing["id"],
            "filename": filename,
            "imported": 0,
            "duplicates": 0,
            "message": "This exact file was already imported.",
        }

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

        merchant = normalize_merchant(tx.counterparty_name or tx.description)
        direction = "inflow" if tx.amount >= 0 else "outflow"
        counterparty_account_hash = None
        if tx.counterparty_account:
            candidate_hash = stable_hash(tx.counterparty_account)
            if candidate_hash != account_hash:
                counterparty_account_hash = candidate_hash
        source_fingerprint = fingerprint(
            [
                str(account_id),
                tx.transaction_date,
                f"{tx.amount:.2f}",
                tx.currency,
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
                tx.amount,
                tx.currency,
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
        imported += 1
        if duplicate_row:
            conn.execute(
                """
                INSERT OR IGNORE INTO transaction_links (
                    link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
                ) VALUES ('duplicate', ?, ?, ?, 1.0, 'Same account/date/amount/counterparty/reference fingerprint')
                """,
                (normalized_id, duplicate_row["id"], abs(tx.amount)),
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
