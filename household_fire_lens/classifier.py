from __future__ import annotations

import calendar
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .database import json_dumps, json_loads


ECONOMIC_CLASSES = {
    "income",
    "household_spend",
    "wealth_allocation",
    "internal_transfer",
    "debt_service",
    "reimbursement_pass_through",
    "refund",
    "ignore_noise",
    "needs_review",
}


MERCHANT_CATEGORY_RULES: List[Tuple[Tuple[str, ...], str, str]] = [
    (("ALBERT HEIJN", "AH TO GO", "JUMBO", "LIDL", "ALDI", "PLUS SUPERMARKT", "DIRK"), "Groceries", ""),
    (("RESTAURANT", "CAFE", "BAR ", "UBER EATS", "DELIVEROO", "THUISBEZORGD", "MCDONALD", "BURGER", "PIZZA"), "Eating Out", ""),
    (("NS ", "NS-", "OV-CHIP", "OVPAY", "SHELL", "BP ", "ESSO", "PARKING", "Q-PARK", "UBER", "BOLT"), "Transportation", ""),
    (("SPOTIFY", "NETFLIX", "APPLE.COM/BILL", "GOOGLE", "ICLOUD", "PATREON", "SUBSCRIPTION"), "Subscriptions", ""),
    (("ENERGIE", "VATTENFALL", "ENECO", "WATER", "INTERNET", "ZIGGO", "KPN", "ODIDO"), "Housing", "Utilities"),
    (("INSURANCE", "VERZEKERING", "ALLIANZ", "AON", "ASR", "NN "), "Housing", "Insurance"),
    (("APOTHEEK", "PHARMACY", "HOSPITAL", "ZORG", "DENTIST", "TANDARTS"), "Health", ""),
    (("AMAZON", "BOL.COM", "IKEA", "H&M", "ZARA", "COOLBLUE", "MEDIA MARKT"), "Shopping", ""),
    (("BELASTING", "TAX", "GEMEENTE", "WATERNSCHAP", "WATERSCHAP"), "Taxes and Government", ""),
    (("BANK", "FEE", "KOSTEN", "RENTE"), "Banking and Fees", ""),
]


INVESTMENT_KEYWORDS = ("IBKR", "INTERACTIVE BROKERS", "DEGIRO", "DE GIRO", "BROKER", "LYNX")
MORTGAGE_KEYWORDS = ("MORTGAGE", "HYPOTHEEK", "HYPOTHECAIR", "HYPOTHEEKRENTE")
BOOKING_REIMBURSEMENT_KEYWORDS = ("BOOKING.COM", "BOOKING COM", "BOOKINGCOM", "BOOKING")
CARD_KEYWORDS = ("CREDITCARD", "CREDIT CARD", "MASTERCARD", "VISA", "ICS", "AMEX", "AMERICAN EXPRESS")
REFUND_KEYWORDS = ("REFUND", "RETOUR", "TERUGBETALING", "REVERSAL", "STORNO", "CREDITNOTA")
SAVINGS_KEYWORDS = ("SAVINGS", "SPAAR", "EIGEN REKENING", "OWN ACCOUNT")
RISKY_REVIEW_CLASSES = {"wealth_allocation", "internal_transfer", "reimbursement_pass_through", "ignore_noise"}
GENERIC_MERCHANT_SCOPES = {"", "SEPA", "SEPA OVERBOEKING", "TRANSACTION", "TRANSFER", "OVERSCHRIJVING", "INCASSO"}


@dataclass
class Annotation:
    economic_class: str
    category: str = "Uncategorized"
    subcategory: str = ""
    confidence: float = 0.5
    explanation: str = ""
    rule_id: Optional[int] = None
    review_status: str = "auto"


def classify_all(conn: sqlite3.Connection) -> Dict[str, int]:
    conn.execute("DELETE FROM transaction_annotations")
    conn.execute("DELETE FROM transaction_links WHERE link_type != 'duplicate'")
    conn.execute("DELETE FROM review_items WHERE status = 'open'")
    transactions = load_transactions(conn)
    salary_ids = detect_salary_ids(transactions)
    transfer_pairs = detect_transfer_pairs(transactions)
    refund_pairs = detect_refund_pairs(transactions)

    linked_transfer_ids = set()
    for left_id, right_id, amount, kind, explanation in transfer_pairs:
        linked_transfer_ids.add(left_id)
        linked_transfer_ids.add(right_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_links (
                link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
            ) VALUES (?, ?, ?, ?, 0.96, ?)
            """,
            (kind, left_id, right_id, amount, explanation),
        )

    refund_category_by_id: Dict[int, Tuple[str, str]] = {}
    for refund_id, original_id, amount, category, subcategory in refund_pairs:
        refund_category_by_id[refund_id] = (category, subcategory)
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_links (
                link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
            ) VALUES ('refund_pair', ?, ?, ?, 0.88, 'Matched refund to previous similar merchant outflow')
            """,
            (refund_id, original_id, amount),
        )

    user_rules = load_enabled_rules(conn)
    counts: Dict[str, int] = defaultdict(int)
    for tx in transactions:
        annotation = classify_transaction(
            tx,
            salary_ids=salary_ids,
            linked_transfer_ids=linked_transfer_ids,
            refund_category_by_id=refund_category_by_id,
            user_rules=user_rules,
        )
        counts[annotation.economic_class] += 1
        conn.execute(
            """
            INSERT OR REPLACE INTO transaction_annotations (
                transaction_id, economic_class, category, subcategory, confidence,
                rule_id, review_status, explanation, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                tx["id"],
                annotation.economic_class,
                annotation.category,
                annotation.subcategory,
                annotation.confidence,
                annotation.rule_id,
                annotation.review_status,
                annotation.explanation,
            ),
        )

    create_review_items(conn)
    conn.commit()
    return dict(counts)


def load_transactions(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT
            nt.*,
            a.role AS account_role,
            a.institution,
            a.display_name AS account_name
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        ORDER BY nt.transaction_date, nt.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def load_enabled_rules(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        "SELECT * FROM classification_rules WHERE enabled = 1 ORDER BY priority ASC, id ASC"
    ).fetchall()
    rules = []
    for row in rows:
        rule = dict(row)
        rule["conditions"] = json_loads(rule["conditions_json"], {})
        rule["actions"] = json_loads(rule["actions_json"], {})
        rules.append(rule)
    return rules


def classify_transaction(
    tx: Dict,
    salary_ids: Iterable[int],
    linked_transfer_ids: Iterable[int],
    refund_category_by_id: Dict[int, Tuple[str, str]],
    user_rules: List[Dict],
) -> Annotation:
    text = tx_text(tx)
    amount = float(tx["amount"])
    role = tx["account_role"]
    salary_set = set(salary_ids)
    transfer_set = set(linked_transfer_ids)

    if tx["is_duplicate"]:
        return Annotation("ignore_noise", "Duplicate", "", 1.0, "Duplicate import fingerprint", review_status="auto")

    for rule in user_rules:
        if rule_matches(tx, rule["conditions"]):
            actions = rule["actions"]
            return Annotation(
                actions.get("economic_class", "household_spend"),
                actions.get("category", "Uncategorized"),
                actions.get("subcategory", ""),
                float(rule["confidence"]),
                f"User rule: {rule['name']}",
                rule_id=rule["id"],
            )

    if tx["id"] in salary_set:
        return Annotation("income", "Income", "Salary", 0.94, "Recurring salary pattern: payer/date window/amount similarity")

    if any(keyword in text for keyword in BOOKING_REIMBURSEMENT_KEYWORDS) and amount > 0:
        return Annotation("reimbursement_pass_through", "Reimbursements", "Booking.com", 0.93, "Booking.com reimbursement deposit")

    if tx["id"] in refund_category_by_id or (amount > 0 and any(keyword in text for keyword in REFUND_KEYWORDS)):
        category, subcategory = refund_category_by_id.get(tx["id"], ("Uncategorized", ""))
        return Annotation("refund", category, subcategory, 0.84, "Refund reduces original category when matched")

    if tx["id"] in transfer_set:
        if role == "investment" or any(keyword in text for keyword in INVESTMENT_KEYWORDS):
            return Annotation("wealth_allocation", "Investments", "", 0.96, "Matched own-account investment transfer")
        return Annotation("internal_transfer", "Transfers", "", 0.96, "Matched own-account transfer pair")

    if role == "investment":
        return Annotation("wealth_allocation", "Investments", "", 0.78, "Investment account activity")

    if any(keyword in text for keyword in INVESTMENT_KEYWORDS):
        return Annotation("wealth_allocation", "Investments", "", 0.9, "Investment account or broker keyword")

    if any(keyword in text for keyword in MORTGAGE_KEYWORDS):
        return Annotation("debt_service", "Housing", "Mortgage", 0.92, "Mortgage keyword")

    if amount < 0 and any(keyword in text for keyword in CARD_KEYWORDS):
        return Annotation("household_spend", "Unknown Card Spend", "", 0.72, "Credit card settlement; detailed card import optional")

    if any(keyword in text for keyword in SAVINGS_KEYWORDS):
        return Annotation("internal_transfer", "Transfers", "Savings", 0.74, "Savings or own-account keyword")

    if amount > 0:
        return Annotation("needs_review", "Uncategorized", "", 0.45, "Positive transaction is not salary or reimbursement")

    category, subcategory, confidence, explanation = categorize_merchant(text)
    if category:
        return Annotation("household_spend", category, subcategory, confidence, explanation)

    if abs(amount) >= 50:
        return Annotation("needs_review", "Uncategorized", "", 0.4, "Material outflow needs classification")
    return Annotation("household_spend", "Uncategorized", "", 0.46, "Low-value uncategorized spend")


def tx_text(tx: Dict) -> str:
    return " ".join(
        str(part or "").upper()
        for part in [
            tx.get("counterparty_name"),
            tx.get("description"),
            tx.get("normalized_merchant"),
            tx.get("reference"),
            tx.get("account_name"),
            tx.get("institution"),
        ]
    )


def tx_month(tx: Dict) -> str:
    return str(tx["transaction_date"])[:7]


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_salary_window(day: date) -> bool:
    if day.month == 12:
        return 18 <= day.day <= 26
    expected = 25
    weekday = date(day.year, day.month, 25).weekday()
    if weekday == calendar.SATURDAY:
        return day.day in {24, 25}
    if weekday == calendar.SUNDAY:
        return day.day in {25, 26}
    return 24 <= day.day <= 26


def detect_salary_ids(transactions: List[Dict]) -> set:
    by_merchant: Dict[str, List[Dict]] = defaultdict(list)
    for tx in transactions:
        if tx["is_duplicate"] or float(tx["amount"]) <= 0:
            continue
        text = tx_text(tx)
        merchant = tx.get("normalized_merchant") or tx.get("counterparty_name") or "UNKNOWN"
        tx_date = parse_iso_date(tx["transaction_date"])
        if is_salary_window(tx_date) or any(word in text for word in ("SALARY", "SALARIS", "PAYROLL", "LOON")):
            by_merchant[merchant].append(tx)

    salary_ids = set()
    for merchant, items in by_merchant.items():
        months = {tx_month(tx) for tx in items}
        if len(months) < 2 and not any("SALARY" in tx_text(tx) or "SALARIS" in tx_text(tx) for tx in items):
            continue
        amounts = [float(tx["amount"]) for tx in items]
        median = sorted(amounts)[len(amounts) // 2]
        for tx in items:
            amount = float(tx["amount"])
            tolerance = max(250.0, abs(median) * 0.12)
            if abs(amount - median) <= tolerance or any(word in tx_text(tx) for word in ("SALARY", "SALARIS", "PAYROLL", "LOON")):
                salary_ids.add(tx["id"])
    return salary_ids


def detect_transfer_pairs(transactions: List[Dict]) -> List[Tuple[int, int, float, str, str]]:
    pairs = []
    active = [tx for tx in transactions if not tx["is_duplicate"]]
    for i, left in enumerate(active):
        left_amount = float(left["amount"])
        if left_amount == 0:
            continue
        left_date = parse_iso_date(left["transaction_date"])
        for right in active[i + 1 :]:
            right_amount = float(right["amount"])
            if left["account_id"] == right["account_id"]:
                continue
            if abs(left_amount + right_amount) > 0.01:
                continue
            right_date = parse_iso_date(right["transaction_date"])
            if abs((left_date - right_date).days) > 3:
                continue
            roles = {left["account_role"], right["account_role"]}
            texts = tx_text(left) + " " + tx_text(right)
            if "investment" in roles or any(keyword in texts for keyword in INVESTMENT_KEYWORDS):
                kind = "transfer_pair"
                explanation = "Matched equal/opposite investment transfer across own accounts"
            elif roles <= {"checking", "savings", "unknown"} or any(keyword in texts for keyword in SAVINGS_KEYWORDS):
                kind = "transfer_pair"
                explanation = "Matched equal/opposite transfer across own accounts"
            else:
                continue
            pairs.append((left["id"], right["id"], abs(left_amount), kind, explanation))
            break
    return pairs


def detect_refund_pairs(transactions: List[Dict]) -> List[Tuple[int, int, float, str, str]]:
    outflows = [tx for tx in transactions if float(tx["amount"]) < 0 and not tx["is_duplicate"]]
    inflows = [tx for tx in transactions if float(tx["amount"]) > 0 and not tx["is_duplicate"]]
    pairs = []
    for refund in inflows:
        refund_text = tx_text(refund)
        if not any(keyword in refund_text for keyword in REFUND_KEYWORDS):
            continue
        refund_date = parse_iso_date(refund["transaction_date"])
        refund_merchant = refund.get("normalized_merchant") or ""
        best = None
        for original in outflows:
            original_date = parse_iso_date(original["transaction_date"])
            if original_date > refund_date:
                continue
            if (refund_date - original_date).days > 120:
                continue
            if abs(abs(float(original["amount"])) - float(refund["amount"])) > max(10.0, float(refund["amount"]) * 0.2):
                continue
            original_merchant = original.get("normalized_merchant") or ""
            if refund_merchant and original_merchant and not merchant_overlap(refund_merchant, original_merchant):
                continue
            best = original
            break
        if best:
            category, subcategory, _, _ = categorize_merchant(tx_text(best))
            pairs.append((refund["id"], best["id"], float(refund["amount"]), category or "Uncategorized", subcategory))
    return pairs


def merchant_overlap(left: str, right: str) -> bool:
    left_words = {word for word in left.split() if len(word) >= 3}
    right_words = {word for word in right.split() if len(word) >= 3}
    return bool(left_words & right_words)


def categorize_merchant(text: str) -> Tuple[str, str, float, str]:
    for keywords, category, subcategory in MERCHANT_CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            matched = next(keyword for keyword in keywords if keyword in text)
            return category, subcategory, 0.82, f"Merchant keyword matched: {matched}"
    return "", "", 0.0, ""


def rule_matches(tx: Dict, conditions: Dict) -> bool:
    if not rule_is_safely_scoped(conditions):
        return False
    text = tx_text(tx)
    transaction_id = conditions.get("transaction_id")
    merchant_contains = str(conditions.get("merchant_contains", "")).upper()
    description_contains = str(conditions.get("description_contains", "")).upper()
    min_amount = conditions.get("min_abs_amount")
    account_role = conditions.get("account_role")
    direction = conditions.get("direction")
    if transaction_id is not None and int(transaction_id) != int(tx["id"]):
        return False
    if merchant_contains and merchant_contains not in text:
        return False
    if description_contains and description_contains not in text:
        return False
    if min_amount is not None and abs(float(tx["amount"])) < float(min_amount):
        return False
    if account_role and tx["account_role"] != account_role:
        return False
    if direction and tx["direction"] != direction:
        return False
    return True


def rule_is_safely_scoped(conditions: Dict) -> bool:
    if conditions.get("transaction_id") is not None:
        return True
    merchant_contains = str(conditions.get("merchant_contains", "")).strip().upper()
    description_contains = str(conditions.get("description_contains", "")).strip().upper()
    account_role = str(conditions.get("account_role", "")).strip()
    if merchant_is_safe_scope(merchant_contains):
        return True
    if len(description_contains) >= 8:
        return True
    if account_role and (merchant_is_safe_scope(merchant_contains) or len(description_contains) >= 8):
        return True
    return False


def merchant_is_safe_scope(merchant: str) -> bool:
    merchant = " ".join(merchant.strip().upper().split())
    if len(merchant) < 4:
        return False
    if merchant in GENERIC_MERCHANT_SCOPES:
        return False
    return any(char.isalpha() for char in merchant)


def create_review_items(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT nt.id, nt.amount, nt.normalized_merchant, ta.economic_class, ta.confidence, ta.explanation
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND (
            ta.economic_class = 'needs_review'
            OR ta.confidence < 0.55
            OR (ta.category = 'Uncategorized' AND ABS(nt.amount) >= 100)
          )
        ORDER BY ABS(nt.amount) DESC
        """
    ).fetchall()
    for row in rows:
        amount = abs(float(row["amount"]))
        issue_type = "classification"
        if row["economic_class"] == "needs_review":
            issue_type = "needs_review"
        suggested = {
            "economic_class": "household_spend" if float(row["amount"]) < 0 else "income",
            "category": "Uncategorized",
            "create_rule": bool(row["normalized_merchant"]),
        }
        conn.execute(
            """
            INSERT INTO review_items (
                transaction_id, issue_type, materiality, suggested_action_json, reason, status
            ) VALUES (?, ?, ?, ?, ?, 'open')
            """,
            (row["id"], issue_type, amount, json_dumps(suggested), row["explanation"]),
        )


def create_rule_from_review(
    conn: sqlite3.Connection,
    transaction_id: int,
    economic_class: str,
    category: str,
    subcategory: str = "",
    merchant_scope: bool = True,
) -> int:
    tx = conn.execute(
        "SELECT normalized_merchant, direction FROM normalized_transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    merchant = tx["normalized_merchant"] or ""
    if economic_class in RISKY_REVIEW_CLASSES or not (merchant_scope and merchant_is_safe_scope(merchant)):
        conditions = {"transaction_id": transaction_id}
    else:
        conditions = {"merchant_contains": merchant, "direction": tx["direction"]}
    actions = {
        "economic_class": economic_class,
        "category": category,
        "subcategory": subcategory,
    }
    cursor = conn.execute(
        """
        INSERT INTO classification_rules (
            name, priority, conditions_json, actions_json, confidence, created_by, enabled
        ) VALUES (?, 50, ?, ?, 0.96, 'user', 1)
        """,
        (f"Classify {merchant or 'transaction'} as {category}", json_dumps(conditions), json_dumps(actions)),
    )
    return int(cursor.lastrowid)
