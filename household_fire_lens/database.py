from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


SCHEMA_VERSION = 1


def connect_database(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution TEXT NOT NULL,
            account_hint TEXT,
            filename TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            statement_year INTEGER,
            parser_version TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'imported'
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'unknown',
            currency TEXT NOT NULL DEFAULT 'EUR',
            account_identifier_hash TEXT NOT NULL,
            is_own_account INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(institution, account_identifier_hash)
        );

        CREATE TABLE IF NOT EXISTS raw_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER NOT NULL REFERENCES source_files(id),
            row_number INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_file_id, row_hash)
        );

        CREATE TABLE IF NOT EXISTS normalized_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_transaction_id INTEGER NOT NULL REFERENCES raw_transactions(id),
            source_file_id INTEGER NOT NULL REFERENCES source_files(id),
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            transaction_date TEXT NOT NULL,
            booking_date TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'EUR',
            direction TEXT NOT NULL,
            counterparty_name TEXT,
            counterparty_account_hash TEXT,
            description TEXT,
            normalized_merchant TEXT,
            reference TEXT,
            source_fingerprint TEXT NOT NULL,
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_normalized_date ON normalized_transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_normalized_fingerprint ON normalized_transactions(source_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_normalized_account ON normalized_transactions(account_id);

        CREATE TABLE IF NOT EXISTS transaction_annotations (
            transaction_id INTEGER PRIMARY KEY REFERENCES normalized_transactions(id),
            economic_class TEXT NOT NULL,
            category TEXT,
            subcategory TEXT,
            confidence REAL NOT NULL,
            rule_id INTEGER,
            review_status TEXT NOT NULL DEFAULT 'auto',
            explanation TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS classification_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            conditions_json TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.9,
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS transaction_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_type TEXT NOT NULL,
            from_transaction_id INTEGER NOT NULL REFERENCES normalized_transactions(id),
            to_transaction_id INTEGER NOT NULL REFERENCES normalized_transactions(id),
            amount REAL NOT NULL,
            confidence REAL NOT NULL,
            explanation TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(link_type, from_transaction_id, to_transaction_id)
        );

        CREATE TABLE IF NOT EXISTS amortization_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            merchant_pattern TEXT,
            transaction_id INTEGER REFERENCES normalized_transactions(id),
            annual_amount REAL NOT NULL,
            monthly_amount REAL NOT NULL,
            start_month TEXT NOT NULL,
            end_month TEXT,
            confidence REAL NOT NULL DEFAULT 0.8,
            review_status TEXT NOT NULL DEFAULT 'suggested',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER REFERENCES normalized_transactions(id),
            issue_type TEXT NOT NULL,
            materiality REAL NOT NULL DEFAULT 0,
            suggested_action_json TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS monthly_snapshots (
            month TEXT PRIMARY KEY,
            real_income REAL NOT NULL,
            household_spend_cashflow REAL NOT NULL,
            household_spend_normalized REAL NOT NULL,
            mortgage_total REAL NOT NULL,
            mortgage_principal_estimate REAL NOT NULL,
            wealth_allocation REAL NOT NULL,
            internal_transfers REAL NOT NULL,
            reimbursements_received REAL NOT NULL,
            reimbursements_cleared REAL NOT NULL,
            refunds REAL NOT NULL,
            net_cash_change REAL NOT NULL,
            savings_rate_cashflow REAL,
            savings_rate_fire REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Optional[str], default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def fetch_all(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return rows_to_dicts(conn.execute(sql, params).fetchall())


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    row = conn.execute(sql, params).fetchone()
    return row_to_dict(row) if row else None
