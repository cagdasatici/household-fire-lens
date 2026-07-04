from __future__ import annotations

import argparse
import cgi
import errno
import json
import mimetypes
import os
import subprocess
import sys
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .aggregation import (
    fire_snapshot,
    list_amortization_rules,
    optimization_insights,
    recompute_monthly_snapshots,
    recurring_merchants,
    spending_breakdown,
)
from .classifier import classify_all, create_rule_from_review, review_group_key
from .database import connect_database, fetch_all, fetch_one, json_dumps, json_loads
from .entity_resolver import enrich_candidate_merchants, store_user_entity_mapping
from .importer import import_csv


APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"
RUN_DIR = APP_ROOT / ".household-fire-lens"
DEFAULT_DB = RUN_DIR / "household-fire-lens.sqlite3"
DEFAULT_PORT = 8787
PIDFILE = RUN_DIR / "server.pid"


def get_database_path() -> str:
    return os.environ.get("HOUSEHOLD_FIRE_LENS_DB", str(DEFAULT_DB))


def git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=APP_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def app_metadata(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    classified_at = None
    if conn:
        row = conn.execute(
            """
            SELECT MAX(updated_at) AS classified_at
            FROM transaction_annotations
            """
        ).fetchone()
        classified_at = row["classified_at"] if row else None
    return {
        "git_hash": git_revision(),
        "database": str(Path(get_database_path()).resolve()),
        "classified_at": classified_at,
        "pid": os.getpid(),
    }


class HouseholdFireLensHandler(BaseHTTPRequestHandler):
    server_version = "HouseholdFireLens/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self.handle_api_get(parsed.path, parse_qs(parsed.query))
            else:
                self.serve_static(parsed.path)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/imports":
                self.handle_import()
            elif parsed.path.startswith("/api/review-items/") and parsed.path.endswith("/resolve"):
                review_id = int(parsed.path.split("/")[3])
                self.handle_review_resolve(review_id)
            elif parsed.path == "/api/rules":
                self.handle_create_rule()
            elif parsed.path.startswith("/api/amortization-rules/"):
                rule_id = int(parsed.path.split("/")[3])
                self.handle_amortization_status(rule_id)
            elif parsed.path == "/api/reclassify":
                self.handle_reclassify()
            elif parsed.path == "/api/entity-enrichment/run":
                self.handle_entity_enrichment()
            else:
                self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/accounts/"):
                account_id = int(parsed.path.split("/")[-1])
                self.handle_account_patch(account_id)
            elif parsed.path.startswith("/api/rules/"):
                rule_id = int(parsed.path.split("/")[-1])
                self.handle_rule_patch(rule_id)
            else:
                self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[household-fire-lens] {self.address_string()} - {fmt % args}")

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self.server, "conn"):
            self.server.conn = connect_database(get_database_path())  # type: ignore[attr-defined]
        return self.server.conn  # type: ignore[attr-defined]

    def handle_api_get(self, path: str, query: Dict[str, Any]) -> None:
        if path == "/api/health":
            self.send_json({"ok": True, **app_metadata(self.conn)})
        elif path == "/api/metadata":
            self.send_json(app_metadata(self.conn))
        elif path == "/api/imports":
            self.send_json({"imports": fetch_all(self.conn, "SELECT * FROM source_files ORDER BY imported_at DESC")})
        elif path == "/api/accounts":
            self.send_json({"accounts": fetch_all(self.conn, "SELECT * FROM accounts ORDER BY institution, display_name")})
        elif path == "/api/transactions":
            self.send_json({"transactions": self.list_transactions(query)})
        elif path == "/api/review-items":
            self.send_json({"review_items": self.list_review_items()})
        elif path.startswith("/api/review-items/") and path.endswith("/transactions"):
            review_id = int(path.split("/")[3])
            self.send_json({"transactions": self.list_review_group_transactions(review_id)})
        elif path == "/api/dashboard/fire":
            fire_multiple = float((query.get("multiple") or ["25"])[0])
            self.send_json(fire_snapshot(self.conn, fire_multiple))
        elif path == "/api/dashboard/monthly-flow":
            recompute_monthly_snapshots(self.conn)
            self.send_json({"months": fetch_all(self.conn, "SELECT * FROM monthly_snapshots ORDER BY month")})
        elif path == "/api/dashboard/spending":
            self.send_json(spending_breakdown(self.conn))
        elif path == "/api/dashboard/optimization":
            recompute_monthly_snapshots(self.conn)
            self.send_json(optimization_insights(self.conn))
        elif path == "/api/recurring":
            self.send_json({"recurring": recurring_merchants(self.conn)})
        elif path == "/api/amortization-rules":
            recompute_monthly_snapshots(self.conn)
            self.send_json({"amortization_rules": list_amortization_rules(self.conn)})
        elif path == "/api/data-health":
            self.send_json(fire_snapshot(self.conn)["data_health"])
        elif path == "/api/rules":
            self.send_json({"rules": fetch_all(self.conn, "SELECT * FROM classification_rules ORDER BY priority, id")})
        elif path == "/api/rule-audit":
            self.send_json({"rules": self.rule_audit()})
        elif path == "/api/entity-enrichment":
            self.send_json(
                {
                    "cache": fetch_all(
                        self.conn,
                        """
                        SELECT lookup_key, merchant_name, source, source_url, label, description,
                               economic_class, category, subcategory, confidence, status, updated_at
                        FROM entity_enrichment_cache
                        ORDER BY updated_at DESC, lookup_key
                        LIMIT 250
                        """,
                    )
                }
            )
        else:
            self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def handle_import(self) -> None:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type"),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        file_item = form["file"] if "file" in form else None
        if isinstance(file_item, list):
            file_item = file_item[0] if file_item else None
        if file_item is None or not getattr(file_item, "file", None):
            self.send_json({"error": "Missing file field"}, status=HTTPStatus.BAD_REQUEST)
            return
        filename = Path(file_item.filename or "upload.csv").name
        content = file_item.file.read()
        institution = form.getfirst("institution") or None
        account_role = form.getfirst("account_role") or None
        account_hint = form.getfirst("account_hint") or ""
        result = import_csv(self.conn, filename, content, institution, account_role, account_hint)
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json(result)

    def handle_account_patch(self, account_id: int) -> None:
        body = self.read_json()
        allowed_roles = {
            "checking",
            "savings",
            "investment",
            "mortgage",
            "credit_card",
            "credit_card_proxy",
            "wise",
            "broker_proxy",
            "unknown",
        }
        allowed_owners = {"self", "partner", "joint", "known_counterparty"}
        role = body.get("role")
        owner = body.get("owner")
        display_name = body.get("display_name")
        if role and role not in allowed_roles:
            self.send_json({"error": f"Invalid role: {role}"}, status=HTTPStatus.BAD_REQUEST)
            return
        if owner and owner not in allowed_owners:
            self.send_json({"error": f"Invalid owner: {owner}"}, status=HTTPStatus.BAD_REQUEST)
            return
        if role:
            self.conn.execute("UPDATE accounts SET role = ? WHERE id = ?", (role, account_id))
        if owner:
            self.conn.execute("UPDATE accounts SET owner = ? WHERE id = ?", (owner, account_id))
        if display_name:
            self.conn.execute("UPDATE accounts SET display_name = ? WHERE id = ?", (display_name, account_id))
        self.conn.commit()
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"account": fetch_one(self.conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))})

    def handle_review_resolve(self, review_id: int) -> None:
        body = self.read_json()
        tx_id = int(body.get("transaction_id") or 0)
        if not tx_id:
            row = self.conn.execute("SELECT transaction_id FROM review_items WHERE id = ?", (review_id,)).fetchone()
            if not row:
                self.send_json({"error": "Review item not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not row["transaction_id"]:
                self.send_json(
                    {"error": "This review item is an expected-income check and needs an import or source correction."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            tx_id = int(row["transaction_id"])
        economic_class = body.get("economic_class", "household_spend")
        category = body.get("category", "Uncategorized")
        subcategory = body.get("subcategory", "")
        create_rule = bool(body.get("create_rule", True))
        rule_id = None
        if create_rule:
            rule_id = create_rule_from_review(self.conn, tx_id, economic_class, category, subcategory)
        tx = fetch_one(
            self.conn,
            """
            SELECT normalized_merchant, counterparty_name
            FROM normalized_transactions
            WHERE id = ?
            """,
            (tx_id,),
        )
        merchant_name = ""
        if tx:
            merchant_name = tx.get("normalized_merchant") or tx.get("counterparty_name") or ""
        mapping_stored = False
        if merchant_name:
            mapping_stored = store_user_entity_mapping(
                self.conn,
                merchant_name,
                economic_class,
                category,
                subcategory,
            )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO transaction_annotations (
                transaction_id, economic_class, category, subcategory, confidence, rule_id,
                review_status, digest_tier, explanation, updated_at
            ) VALUES (?, ?, ?, ?, 0.99, ?, 'reviewed', 'reviewed', 'User review decision', CURRENT_TIMESTAMP)
            """,
            (tx_id, economic_class, category, subcategory, rule_id),
        )
        self.conn.execute(
            "UPDATE review_items SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (review_id,),
        )
        self.conn.commit()
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"resolved": True, "rule_id": rule_id, "mapping_stored": mapping_stored})

    def handle_create_rule(self) -> None:
        body = self.read_json()
        name = body.get("name") or "Custom rule"
        conditions = body.get("conditions") or {}
        actions = body.get("actions") or {}
        confidence = float(body.get("confidence", 0.95))
        created_by = body.get("created_by") or "user"
        if created_by not in {"user", "agent", "system"}:
            self.send_json({"error": "created_by must be user, agent, or system"}, status=HTTPStatus.BAD_REQUEST)
            return
        cursor = self.conn.execute(
            """
            INSERT INTO classification_rules (
                name, priority, conditions_json, actions_json, confidence, created_by, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                name,
                int(body.get("priority", 50)),
                json_dumps(conditions),
                json_dumps(actions),
                confidence,
                created_by,
            ),
        )
        self.conn.commit()
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"rule_id": cursor.lastrowid})

    def handle_rule_patch(self, rule_id: int) -> None:
        body = self.read_json()
        updates = []
        params = []
        for column in ("name", "priority", "confidence", "enabled"):
            if column in body:
                updates.append(f"{column} = ?")
                params.append(body[column])
        if "conditions" in body:
            updates.append("conditions_json = ?")
            params.append(json_dumps(body["conditions"]))
        if "actions" in body:
            updates.append("actions_json = ?")
            params.append(json_dumps(body["actions"]))
        if not updates:
            self.send_json({"error": "No rule updates supplied"}, status=HTTPStatus.BAD_REQUEST)
            return
        params.append(rule_id)
        self.conn.execute(f"UPDATE classification_rules SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"rule": fetch_one(self.conn, "SELECT * FROM classification_rules WHERE id = ?", (rule_id,))})

    def handle_amortization_status(self, rule_id: int) -> None:
        body = self.read_json()
        status = body.get("review_status") or body.get("status")
        if status not in {"approved", "disabled", "suggested", "auto"}:
            self.send_json({"error": "Status must be approved, disabled, suggested, or auto"}, status=HTTPStatus.BAD_REQUEST)
            return
        self.conn.execute(
            "UPDATE amortization_rules SET review_status = ? WHERE id = ?",
            (status, rule_id),
        )
        self.conn.commit()
        recompute_monthly_snapshots(self.conn)
        self.send_json(
            {"rule": fetch_one(self.conn, "SELECT * FROM amortization_rules WHERE id = ?", (rule_id,))}
        )

    def handle_reclassify(self) -> None:
        counts = classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"classified": counts})

    def handle_entity_enrichment(self) -> None:
        body = self.read_json()
        limit = max(1, min(int(body.get("limit", 10)), 25))
        summary = enrich_candidate_merchants(self.conn, limit=limit)
        counts = classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        self.send_json({"enrichment": summary, "classified": counts})

    def list_transactions(self, query: Dict[str, Any]) -> Any:
        clauses = ["1 = 1"]
        params = []
        if "month" in query:
            clauses.append("substr(nt.transaction_date, 1, 7) = ?")
            params.append(query["month"][0])
        if "economic_class" in query:
            clauses.append("ta.economic_class = ?")
            params.append(query["economic_class"][0])
        if "category" in query:
            clauses.append("ta.category = ?")
            params.append(query["category"][0])
        if "account_role" in query:
            clauses.append("a.role = ?")
            params.append(query["account_role"][0])
        if "confidence" in query:
            bucket = query["confidence"][0]
            if bucket == "low":
                clauses.append("ta.confidence < 0.65")
            elif bucket == "medium":
                clauses.append("ta.confidence >= 0.65 AND ta.confidence < 0.85")
            elif bucket == "high":
                clauses.append("ta.confidence >= 0.85")
        if "q" in query:
            clauses.append("(nt.description LIKE ? OR nt.normalized_merchant LIKE ? OR nt.counterparty_name LIKE ?)")
            like = f"%{query['q'][0]}%"
            params.extend([like, like, like])
        limit = int((query.get("limit") or ["250"])[0])
        params.append(limit)
        return fetch_all(
            self.conn,
            f"""
            SELECT
                nt.id, nt.transaction_date, nt.amount, nt.currency, nt.counterparty_name,
                nt.description, nt.normalized_merchant, nt.is_duplicate,
                a.display_name AS account_name, a.role AS account_role, a.institution,
                ta.economic_class, ta.category, ta.subcategory, ta.confidence,
                ta.digest_tier, ta.explanation
            FROM normalized_transactions nt
            JOIN accounts a ON a.id = nt.account_id
            LEFT JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE {' AND '.join(clauses)}
            ORDER BY nt.transaction_date DESC, nt.id DESC
            LIMIT ?
            """,
            tuple(params),
        )

    def list_review_items(self) -> Any:
        rows = fetch_all(
            self.conn,
            """
            SELECT
                ri.*,
                nt.transaction_date, nt.amount, nt.description, nt.normalized_merchant,
                a.display_name AS account_name, a.role AS account_role,
                ta.economic_class, ta.category, ta.subcategory, ta.confidence,
                ta.digest_tier,
                eie.month AS expected_month, eie.event_type AS expected_event_type,
                eie.expected_date, eie.expected_amount
            FROM review_items ri
            LEFT JOIN normalized_transactions nt ON nt.id = ri.transaction_id
            LEFT JOIN accounts a ON a.id = nt.account_id
            LEFT JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            LEFT JOIN expected_income_events eie ON eie.id = ri.expected_event_id
            WHERE ri.status = 'open'
            ORDER BY ri.materiality DESC, ri.id
            """,
        )
        for row in rows:
            row["suggested_action"] = json_loads(row.pop("suggested_action_json"), {})
        return rows

    def list_review_group_transactions(self, review_id: int) -> Any:
        review = self.conn.execute(
            """
            SELECT
                ri.transaction_id,
                nt.id, nt.amount, nt.direction, nt.normalized_merchant,
                nt.counterparty_account_hash, nt.description, nt.counterparty_name
            FROM review_items ri
            JOIN normalized_transactions nt ON nt.id = ri.transaction_id
            WHERE ri.id = ?
              AND ri.status = 'open'
            """,
            (review_id,),
        ).fetchone()
        if not review:
            return []
        target_key = review_group_key(review)
        rows = self.conn.execute(
            """
            SELECT
                nt.id, nt.transaction_date, nt.booking_date, nt.amount, nt.currency,
                nt.direction, nt.counterparty_name, nt.counterparty_account_hash,
                nt.description, nt.normalized_merchant, nt.reference,
                a.display_name AS account_name, a.institution, a.role AS account_role,
                ta.economic_class, ta.category, ta.subcategory, ta.confidence,
                ta.digest_tier, ta.explanation
            FROM normalized_transactions nt
            JOIN accounts a ON a.id = nt.account_id
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.is_duplicate = 0
              AND (
                ta.economic_class = 'needs_review'
                OR ta.digest_tier = 'review'
                OR ta.confidence < 0.55
                OR (ta.category = 'Uncategorized' AND ABS(nt.amount) >= 100)
              )
            ORDER BY nt.transaction_date, nt.id
            """
        ).fetchall()
        grouped = []
        for row in rows:
            if review_group_key(row) != target_key:
                continue
            item = dict(row)
            if float(item["amount"]) < 0:
                item["from_account"] = item["account_name"]
                item["to_account"] = item["counterparty_name"] or item["normalized_merchant"] or "Counterparty"
            else:
                item["from_account"] = item["counterparty_name"] or item["normalized_merchant"] or "Counterparty"
                item["to_account"] = item["account_name"]
            grouped.append(item)
        return grouped

    def rule_audit(self) -> Any:
        rows = fetch_all(
            self.conn,
            """
            SELECT
                cr.*,
                COUNT(ta.transaction_id) AS matched_count,
                COALESCE(SUM(ABS(nt.amount)), 0) AS matched_value
            FROM classification_rules cr
            LEFT JOIN transaction_annotations ta ON ta.rule_id = cr.id
            LEFT JOIN normalized_transactions nt ON nt.id = ta.transaction_id
            GROUP BY cr.id
            ORDER BY
                CASE WHEN cr.created_by = 'agent' THEN 0 ELSE 1 END,
                cr.id
            """,
        )
        for row in rows:
            row["conditions"] = json_loads(row.pop("conditions_json"), {})
            row["actions"] = json_loads(row.pop("actions_json"), {})
        return rows

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        safe_path = Path(path.lstrip("/"))
        if ".." in safe_path.parts:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        file_path = WEB_ROOT / safe_path
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except BrokenPipeError:
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Household FIRE Lens dashboard.")
    parser.add_argument("--host", default=os.environ.get("HOUSEHOLD_FIRE_LENS_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HOUSEHOLD_FIRE_LENS_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--db", default=os.environ.get("HOUSEHOLD_FIRE_LENS_DB", str(DEFAULT_DB)))
    return parser.parse_args()


def write_pidfile() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(f"{os.getpid()}\n", encoding="utf-8")


def remove_pidfile() -> None:
    try:
        if PIDFILE.exists() and PIDFILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PIDFILE.unlink()
    except OSError:
        return


def main() -> None:
    args = parse_args()
    host = args.host
    port = args.port
    os.environ["HOUSEHOLD_FIRE_LENS_HOST"] = host
    os.environ["HOUSEHOLD_FIRE_LENS_PORT"] = str(port)
    os.environ["HOUSEHOLD_FIRE_LENS_DB"] = args.db
    try:
        server = HTTPServer((host, port), HouseholdFireLensHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Port conflict: {host}:{port} is already in use. "
                "Stop the existing process or choose --port <free-port>.",
                file=sys.stderr,
            )
            raise SystemExit(98) from exc
        raise
    write_pidfile()
    print(f"Household FIRE Lens running at http://{host}:{port}")
    print(f"Database: {get_database_path()}")
    print(f"PID file: {PIDFILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        if hasattr(server, "conn"):
            server.conn.close()  # type: ignore[attr-defined]
        remove_pidfile()


if __name__ == "__main__":
    main()
