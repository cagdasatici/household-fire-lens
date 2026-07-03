from __future__ import annotations

import calendar
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .database import json_dumps, json_loads
from .entity_resolver import EntityHint, cached_hint_for_merchant, load_entity_hints


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
    (("ALBERT HEIJN", "AH TO GO", "JUMBO", "LIDL", "ALDI", "PLUS SUPERMARKT", "DIRK", "VOMAR", "DEKAMARKT", "HOOGVLIET"), "Groceries", ""),
    (("RESTAURANT", "CAFE", "BAR ", "UBER EATS", "DELIVEROO", "THUISBEZORGD", "MCDONALD", "BURGER", "PIZZA"), "Eating Out", ""),
    (("AIRBNB", "HOTEL", "HOSTEL", "KLM", "TRANSAVIA", "RYANAIR", "EASYJET", "EXPEDIA", "TUI", "SUNWEB", "BOOKING.COM", "BOOKING COM", "VRBO", "FLIGHT", "AIRLINE"), "Holiday", ""),
    (("NS ", "NS-", "OV-CHIP", "OVPAY", "SHELL", "BP ", "ESSO", "PARKING", "Q-PARK", "UBER", "BOLT", "AUTOMOTIVE", "KWIKFIT", "OPONEO", "GARAGE"), "Transportation", ""),
    (("SPOTIFY", "NETFLIX", "APPLE.COM/BILL", "GOOGLE", "ICLOUD", "PATREON", "SUBSCRIPTION"), "Subscriptions", ""),
    (("ENERGIE", "VATTENFALL", "ENECO", "WATER", "INTERNET", "ZIGGO", "KPN", "ODIDO"), "Housing", "Utilities"),
    (("INSURANCE", "VERZEKERING", "ALLIANZ", "AON", "ASR", "NN "), "Housing", "Insurance"),
    (("MEUBEL", "HENDERS EN HAZEL", "KEUKENLOODS", "PRAXIS", "GAMMA", "KARWEI", "FURNITURE", "HOME IMPROVEMENT"), "Home and Furniture", ""),
    (("APOTHEEK", "PHARMACY", "HOSPITAL", "ZORG", "DENTIST", "TANDARTS", "DIERENARTS", "VETERINARY", "EYE WISH", "OPTICIAN", "KRUIDVAT", "ETOS", "TREKPLEISTER"), "Health", ""),
    (("AMAZON", "BOL.COM", "IKEA", "H&M", "H & M", "HEMA", "C&A", "ZARA", "COOLBLUE", "MEDIA MARKT", "DECATHLON", "THEPHONELAB", "ACTION"), "Shopping", ""),
    (("BELASTING", "TAX", "GEMEENTE", "WATERNSCHAP", "WATERSCHAP"), "Taxes and Government", ""),
]


INVESTMENT_KEYWORDS = (
    "IBKR",
    "INTERACTIVE BROKERS",
    "DEGIRO",
    "DE GIRO",
    "FLATEX",
    "FLATEXDEGIRO",
    "TRADE REPUBLIC",
    "BROKER",
    "LYNX",
    "SAXO",
    "BUX",
    "MEESMAN",
    "BRAND NEW DAY",
)
MORTGAGE_KEYWORDS = ("MORTGAGE", "HYPOTHEEK", "HYPOTHECAIR", "HYPOTHEEKRENTE")
BOOKING_REIMBURSEMENT_KEYWORDS = ("BOOKING.COM", "BOOKING COM", "BOOKINGCOM", "BOOKING")
CARD_KEYWORDS = ("CREDITCARD", "CREDIT CARD", "MASTERCARD", "VISA", "ICS", "AMEX", "AMERICAN EXPRESS")
CREDIT_CARD_PAYMENT_KEYWORDS = ("HARTELIJK BEDANKT VOOR UW BETALING", "THANK YOU FOR YOUR PAYMENT", "BETALING", "PAYMENT RECEIVED")
SALARY_KEYWORDS = ("SALARY", "SALARIS", "PAYROLL", "LOON", "WAGE")
BONUS_KEYWORDS = ("BONUS", "CASH BONUS")
REFUND_KEYWORDS = ("REFUND", "RETOUR", "TERUGBETALING", "REVERSAL", "STORNO", "CREDITNOTA", "CASHBACK", "TERUGGAAF", "TERUGBOEKING", "RESTITUTIE")
REIMBURSEMENT_KEYWORDS = ("REIMBURSEMENT", "VERGOEDING", "EXPENSE REIMBURSEMENT")
SUBSIDY_LINK_KEYWORDS = ("RVO", "ISDE", "SUBSIDIE", "SUBSIDY", "CLAIM", "UITKERING", "VERZEKERING", "INSURANCE")
BANK_FEE_KEYWORDS = ("BASISPAKKET", "BETAALPAS", "BETAALPAKKET", "PAKKETKOSTEN", "BANKKOSTEN")
BANK_INTEREST_KEYWORDS = ("CREDITRENTE", "DEBETRENTE")
SAVINGS_KEYWORDS = ("SAVINGS", "SPAAR", "EIGEN REKENING", "OWN ACCOUNT")
CURRENT_ACCOUNT_TRANSFER_KEYWORDS = ("TRANSFER FROM CURRENT ACCOUNT", "TRANSFER TO CURRENT ACCOUNT")
SOCIAL_INSURANCE_KEYWORDS = ("SOCIALE VERZEKERINGSBANK", "SVB")
CHILD_BENEFIT_KEYWORDS = ("KINDERBIJSLAG", "KINDER", "CHILD BENEFIT")
CASH_WITHDRAWAL_KEYWORDS = ("GELDMAAT", "ATM", "CASH WITHDRAWAL", "CONTANTOPNAME", "GELDAUTOMAAT")
CARD_TERMINAL_PROCESSOR_KEYWORDS = ("ZETTLE", "SUMUP", "PAY.NL", "STICHTING MOLLIE PAYMENTS", "MOLLIE PAYMENTS", " VIA MOLLIE", "RIVERTY")
PAYMENT_REQUEST_KEYWORDS = ("TIKKIE", "BETAALVERZOEK", "PAYMENT REQUEST", "BETAALVERZOEKJE")
BANK_TRANSFER_KEYWORDS = ("SEPA OVERBOEKING", "SEPA", "OVERBOEKING", "OVERSCHRIJVING", "BANK TRANSFER")
RISKY_REVIEW_CLASSES = {"wealth_allocation", "internal_transfer", "reimbursement_pass_through", "ignore_noise"}
GENERIC_MERCHANT_SCOPES = {"", "SEPA", "SEPA OVERBOEKING", "TRANSACTION", "TRANSFER", "OVERSCHRIJVING", "INCASSO"}
MAX_OPEN_REVIEW_GROUPS = 150
UNKNOWN_OUTFLOW_REVIEW_THRESHOLD = 250.0
ONE_OFF_INFLOW_REVIEW_THRESHOLD = 500.0
DIRECT_DEBIT_CREDITOR_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}ZZZ[A-Z0-9]{8,}\b")
RECURRING_DIRECT_DEBIT_MIN_OCCURRENCES = 6
RECURRING_DIRECT_DEBIT_AMOUNT_TOLERANCE_PCT = 0.02


@dataclass
class Annotation:
    economic_class: str
    category: str = "Uncategorized"
    subcategory: str = ""
    confidence: float = 0.5
    explanation: str = ""
    rule_id: Optional[int] = None
    review_status: str = "auto"
    digest_tier: str = ""


@dataclass
class RecurringDebitGroup:
    signature_type: str
    signature_value: str
    amount: float
    count: int
    materiality: float


def classify_all(conn: sqlite3.Connection) -> Dict[str, int]:
    conn.execute("DELETE FROM transaction_annotations")
    conn.execute("DELETE FROM transaction_links WHERE link_type != 'duplicate'")
    conn.execute("DELETE FROM review_items WHERE status = 'open'")
    transactions = load_transactions(conn)
    salary_ids = detect_salary_ids(transactions)
    transfer_pairs = detect_transfer_pairs(transactions)
    refund_pairs = detect_refund_pairs(transactions)
    recurring_debit_groups = detect_recurring_direct_debits(transactions)

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
    entity_hints = load_entity_hints(conn)
    counts: Dict[str, int] = defaultdict(int)
    for tx in transactions:
        annotation = classify_transaction(
            tx,
            salary_ids=salary_ids,
            linked_transfer_ids=linked_transfer_ids,
            refund_category_by_id=refund_category_by_id,
            recurring_debit_groups=recurring_debit_groups,
            user_rules=user_rules,
            entity_hints=entity_hints,
        )
        counts[annotation.economic_class] += 1
        digest_tier = annotation.digest_tier or digest_tier_for(annotation)
        conn.execute(
            """
            INSERT OR REPLACE INTO transaction_annotations (
                transaction_id, economic_class, category, subcategory, confidence,
                rule_id, review_status, digest_tier, explanation, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                tx["id"],
                annotation.economic_class,
                annotation.category,
                annotation.subcategory,
                annotation.confidence,
                annotation.rule_id,
                annotation.review_status,
                digest_tier,
                annotation.explanation,
            ),
        )

    link_one_off_inflows(conn)
    create_review_items(conn)
    sync_observed_income_events(conn)
    create_expected_income_reviews(conn)
    conn.commit()
    return dict(counts)


def digest_tier_for(annotation: Annotation) -> str:
    if annotation.review_status == "reviewed":
        return "reviewed"
    if annotation.economic_class == "needs_review":
        return "review"
    if annotation.confidence < 0.70:
        if annotation.category == "Uncategorized":
            return "review"
        return "auto_visible"
    if annotation.confidence >= 0.90:
        return "auto_silent"
    return "auto_visible"


def load_transactions(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT
            nt.*,
            a.role AS account_role,
            a.institution,
            a.display_name AS account_name,
            tad.native_amount,
            tad.native_currency,
            tad.source_currency,
            tad.target_currency
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        LEFT JOIN transaction_amount_details tad ON tad.transaction_id = nt.id
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
    recurring_debit_groups: Dict[int, RecurringDebitGroup],
    user_rules: List[Dict],
    entity_hints: Optional[Dict[str, EntityHint]] = None,
) -> Annotation:
    raw_text = tx_text(tx)
    text = signal_text(tx)
    merchant_text = merchant_match_text(tx)
    amount = float(tx["amount"])
    role = tx["account_role"]
    native_currency = (tx.get("native_currency") or tx.get("currency") or "EUR").upper()
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
        if is_cash_bonus_income(tx):
            return Annotation("income", "Income", "Cash Bonus", 0.94, "February payroll bonus pattern")
        return Annotation("income", "Income", "Salary", 0.94, "Recurring salary pattern: payer/date window/amount similarity")

    if role == "credit_card":
        if amount > 0 and any(keyword in text for keyword in CREDIT_CARD_PAYMENT_KEYWORDS):
            return Annotation("internal_transfer", "Card Settlement", "Amex", 0.86, "Credit-card payment credit imported from card statement")
        return Annotation("ignore_noise", "Credit Card Detail", "Pending Settlement Pairing", 0.9, "Card detail imported; R5 settlement pairing will activate spend lines")

    if role == "wise":
        if amount < 0 and "SHARES" in text:
            return Annotation("wealth_allocation", "Investments", "RSU Settlement", 0.84, "Wise RSU share-booking outflow")
        if amount < 0 and any(keyword in text for keyword in INVESTMENT_KEYWORDS):
            return Annotation("wealth_allocation", "Investments", "Wise to Broker", 0.9, "Wise transfer to broker")
        if amount > 0 and native_currency != "EUR" and amount >= 1000:
            return Annotation("income", "Equity Compensation", "RSU", 0.82, "Large non-EUR Wise inflow treated as RSU proceeds pending vest-schedule check")
        if amount > 0 and native_currency == "EUR":
            return Annotation("internal_transfer", "Inter-account Transfers", "Wise", 0.84, "Wise EUR top-up from own account")

    if role == "savings":
        if any(keyword in text for keyword in CURRENT_ACCOUNT_TRANSFER_KEYWORDS):
            return Annotation("internal_transfer", "Inter-account Transfers", "Savings", 0.92, "Savings account transfer to or from current account")
        if amount > 0 and "INTEREST" in text:
            return Annotation("income", "Interest", "Savings", 0.9, "Savings account interest received")

    if amount > 0 and any(keyword in text for keyword in SOCIAL_INSURANCE_KEYWORDS):
        if any(keyword in text for keyword in CHILD_BENEFIT_KEYWORDS):
            return Annotation("income", "Benefits", "Child Benefit", 0.94, "Dutch SVB child-benefit payment")
        return Annotation("income", "Benefits", "Government Benefit", 0.88, "Dutch SVB payment")

    if any(keyword in text for keyword in BOOKING_REIMBURSEMENT_KEYWORDS) and amount > 0:
        return Annotation("reimbursement_pass_through", "Reimbursements", "Booking.com", 0.93, "Booking.com reimbursement deposit")

    if tx["id"] in refund_category_by_id or (amount > 0 and any(keyword in text for keyword in REFUND_KEYWORDS)):
        category, subcategory = refund_category_by_id.get(tx["id"], ("", ""))
        if not category:
            category, subcategory, _, _ = categorize_merchant(merchant_text)
        return Annotation("refund", category or "Uncategorized", subcategory, 0.84, "Refund reduces original category when matched")

    if tx["id"] in transfer_set:
        if role == "investment" or any(keyword in text for keyword in INVESTMENT_KEYWORDS):
            return Annotation("wealth_allocation", "Investments", "", 0.96, "Matched own-account investment transfer")
        return Annotation("internal_transfer", "Inter-account Transfers", "", 0.96, "Matched own-account transfer pair")

    if role == "investment":
        return Annotation("wealth_allocation", "Investments", "", 0.78, "Investment account activity")

    if any(keyword in text for keyword in INVESTMENT_KEYWORDS):
        return Annotation("wealth_allocation", "Investments", "", 0.9, "Investment account or broker keyword")

    if any(keyword in text for keyword in MORTGAGE_KEYWORDS):
        return Annotation("debt_service", "Housing", "Mortgage", 0.92, "Mortgage keyword")

    if amount < 0 and any(keyword in text for keyword in BANK_FEE_KEYWORDS):
        return Annotation("household_spend", "Banking and Fees", "", 0.84, "Precise bank package or card fee keyword")

    if amount > 0 and "CREDITRENTE" in text:
        return Annotation("income", "Interest", "", 0.8, "Credit interest received")

    if amount < 0 and any(keyword in text for keyword in BANK_INTEREST_KEYWORDS):
        return Annotation("household_spend", "Banking and Fees", "Interest", 0.78, "Precise bank interest keyword")

    if amount < 0 and any(keyword in text for keyword in CARD_KEYWORDS):
        return Annotation("household_spend", "Unknown Card Spend", "", 0.72, "Credit card settlement; detailed card import optional")

    if amount < 0 and any(keyword in text for keyword in CASH_WITHDRAWAL_KEYWORDS):
        return Annotation("household_spend", "Cash Withdrawal", "", 0.86, "ATM or Geldmaat cash withdrawal")

    if any(keyword in text for keyword in SAVINGS_KEYWORDS):
        return Annotation("internal_transfer", "Inter-account Transfers", "Savings", 0.74, "Savings or own-account keyword")

    if any(keyword in text for keyword in PAYMENT_REQUEST_KEYWORDS):
        if amount > 0:
            return Annotation(
                "reimbursement_pass_through",
                "Reimbursements",
                "Payment Request",
                0.72,
                "Payment request settlement received",
            )
        return Annotation("household_spend", "Other", "Payment Request", 0.64, "Payment request paid")

    if amount > 0 and any(keyword in text for keyword in REIMBURSEMENT_KEYWORDS):
        return Annotation("reimbursement_pass_through", "Reimbursements", "Other", 0.72, "Reimbursement-like deposit")

    if amount > 0:
        category, subcategory, _, _ = categorize_merchant(merchant_text)
        if category:
            return Annotation("refund", category, subcategory, 0.7, "Positive merchant credit treated as refund")

    if amount > 0:
        return Annotation("needs_review", "Uncategorized", "", 0.45, "Positive transaction is not salary or reimbursement")

    category, subcategory, confidence, explanation = categorize_merchant(merchant_text)
    if category:
        return Annotation("household_spend", category, subcategory, confidence, explanation)

    if tx["id"] in recurring_debit_groups:
        group = recurring_debit_groups[tx["id"]]
        return Annotation(
            "needs_review",
            "Uncategorized",
            "",
            0.54,
            f"Recurring direct debit needs one classification; grouped {group.count} payments",
        )

    public_hint = cached_hint_for_merchant(entity_hints or {}, tx.get("normalized_merchant") or "")
    if public_hint and amount < 0:
        return Annotation(
            public_hint.economic_class,
            public_hint.category,
            public_hint.subcategory,
            public_hint.confidence,
            f"Free public entity lookup: {public_hint.label or public_hint.source}",
        )

    if amount < 0 and any(keyword in text for keyword in CARD_TERMINAL_PROCESSOR_KEYWORDS):
        return Annotation("household_spend", "Other", "Payment Processor", 0.62, "Payment processor transaction with extracted merchant")

    if amount < 0 and any(keyword in raw_text for keyword in BANK_TRANSFER_KEYWORDS):
        return Annotation("household_spend", "Other", "Bank Transfer", 0.6, "Unmatched outbound bank transfer")

    if abs(amount) >= UNKNOWN_OUTFLOW_REVIEW_THRESHOLD:
        return Annotation("needs_review", "Uncategorized", "", 0.4, "Material outflow needs classification")
    return Annotation("household_spend", "Other", "", 0.58, "Uncategorized outflow below review threshold")


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


BOILERPLATE_PATTERNS = (
    r"\bONLINE BANKING\b",
    r"\bSEPA OVERBOEKING\b",
    r"\bSEPA INCASSO\b",
    r"\bOVERBOEKING\b",
    r"\bOVERSCHRIJVING\b",
    r"\bVALUE DATE\b",
    r"\bDATE/TIME\b",
    r"\bTRTP\b",
    r"\bREMI\b",
    r"\bEREF\b",
    r"\bMARF\b",
    r"\bCSID\b",
    r"\bNAME\b:?",
    r"\bNAAM\b:?",
    r"\bDESCRIPTION\b:?",
    r"\bOMSCHRIJVING\b:?",
    r"\bIBAN\b:?",
    r"\bBIC\b:?",
    r"\bIDEAL\b",
)


def clean_signal_text(value: str) -> str:
    text = str(value or "").upper()
    for pattern in BOILERPLATE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def signal_text(tx: Dict) -> str:
    return clean_signal_text(
        " ".join(
            str(part or "")
            for part in [
                tx.get("counterparty_name"),
                tx.get("description"),
                tx.get("normalized_merchant"),
                tx.get("reference"),
            ]
        )
    )


def merchant_match_text(tx: Dict) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(str(part or "").upper() for part in [tx.get("counterparty_name"), tx.get("normalized_merchant")]),
    ).strip()


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


def has_salary_keyword(tx: Dict) -> bool:
    text = signal_text(tx)
    return any(word in text for word in SALARY_KEYWORDS)


def has_bonus_keyword(tx: Dict) -> bool:
    text = signal_text(tx)
    return any(word in text for word in BONUS_KEYWORDS)


def is_transfer_like_income_candidate(tx: Dict) -> bool:
    text = signal_text(tx)
    return (
        any(keyword in text for keyword in SAVINGS_KEYWORDS)
        or any(keyword in text for keyword in PAYMENT_REQUEST_KEYWORDS)
    )


def is_cash_bonus_income(tx: Dict) -> bool:
    amount = float(tx["amount"])
    tx_date = parse_iso_date(tx["transaction_date"])
    return amount > 0 and (has_bonus_keyword(tx) or (tx_date.month == 2 and has_salary_keyword(tx) and amount >= 15000))


def detect_salary_ids(transactions: List[Dict]) -> set:
    by_merchant: Dict[str, List[Dict]] = defaultdict(list)
    for tx in transactions:
        if tx["is_duplicate"] or float(tx["amount"]) <= 0:
            continue
        merchant = tx.get("normalized_merchant") or tx.get("counterparty_name") or "UNKNOWN"
        tx_date = parse_iso_date(tx["transaction_date"])
        if has_salary_keyword(tx):
            by_merchant[merchant].append(tx)
        elif is_salary_window(tx_date) and float(tx["amount"]) >= 1500 and not is_transfer_like_income_candidate(tx):
            by_merchant[merchant].append(tx)

    salary_ids = set()
    for merchant, items in by_merchant.items():
        months = {tx_month(tx) for tx in items}
        if len(months) < 2 and not any(has_salary_keyword(tx) for tx in items):
            continue
        amounts = [float(tx["amount"]) for tx in items]
        regular_amounts = [amount for tx, amount in zip(items, amounts) if not is_cash_bonus_income(tx)]
        median_source = regular_amounts or amounts
        median = sorted(median_source)[len(median_source) // 2]
        for tx in items:
            amount = float(tx["amount"])
            tolerance = max(250.0, abs(median) * 0.12)
            if abs(amount - median) <= tolerance or has_salary_keyword(tx) or is_cash_bonus_income(tx):
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
            texts = signal_text(left) + " " + signal_text(right)
            if "investment" in roles or any(keyword in texts for keyword in INVESTMENT_KEYWORDS):
                kind = "transfer_pair"
                explanation = "Matched equal/opposite investment transfer across own accounts"
            elif roles <= {"checking", "savings", "wise", "unknown"} or any(keyword in texts for keyword in SAVINGS_KEYWORDS):
                kind = "transfer_pair"
                explanation = "Matched equal/opposite transfer across own accounts"
            else:
                continue
            pairs.append((left["id"], right["id"], abs(left_amount), kind, explanation))
            break
    return pairs


def extract_direct_debit_id(tx: Dict) -> str:
    text = tx_text(tx)
    match = DIRECT_DEBIT_CREDITOR_PATTERN.search(text)
    return match.group(0) if match else ""


def recurring_signature(tx: Dict) -> Tuple[str, str]:
    creditor_id = extract_direct_debit_id(tx)
    if creditor_id:
        return "direct_debit_id", creditor_id
    counterparty_hash = tx.get("counterparty_account_hash") or ""
    if counterparty_hash:
        return "counterparty_account_hash", counterparty_hash
    return "", ""


def recurring_amount_bucket(amount: float) -> float:
    return round(abs(amount) / 10.0) * 10.0


def detect_recurring_direct_debits(transactions: List[Dict]) -> Dict[int, RecurringDebitGroup]:
    grouped: Dict[Tuple[str, str, float], List[Dict]] = defaultdict(list)
    for tx in transactions:
        amount = float(tx["amount"])
        if tx["is_duplicate"] or amount >= 0:
            continue
        signature_type, signature_value = recurring_signature(tx)
        if not signature_value:
            continue
        grouped[(signature_type, signature_value, recurring_amount_bucket(amount))].append(tx)

    result: Dict[int, RecurringDebitGroup] = {}
    for (signature_type, signature_value, _bucket), items in grouped.items():
        if len(items) < RECURRING_DIRECT_DEBIT_MIN_OCCURRENCES:
            continue
        amounts = [abs(float(tx["amount"])) for tx in items]
        average = sum(amounts) / len(amounts)
        if not average:
            continue
        mean_abs_deviation = sum(abs(amount - average) for amount in amounts) / len(amounts)
        if mean_abs_deviation / average > RECURRING_DIRECT_DEBIT_AMOUNT_TOLERANCE_PCT:
            continue
        dates = sorted(parse_iso_date(tx["transaction_date"]) for tx in items)
        monthly_gaps = sum(20 <= (right - left).days <= 45 for left, right in zip(dates, dates[1:]))
        if monthly_gaps < max(4, len(dates) - 2):
            continue
        group = RecurringDebitGroup(
            signature_type=signature_type,
            signature_value=signature_value,
            amount=round(average, 2),
            count=len(items),
            materiality=round(sum(amounts), 2),
        )
        for tx in items:
            result[tx["id"]] = group
    return result


def detect_refund_pairs(transactions: List[Dict]) -> List[Tuple[int, int, float, str, str]]:
    outflows = [tx for tx in transactions if float(tx["amount"]) < 0 and not tx["is_duplicate"]]
    inflows = [tx for tx in transactions if float(tx["amount"]) > 0 and not tx["is_duplicate"]]
    pairs = []
    for refund in inflows:
        refund_text = signal_text(refund)
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
            category, subcategory, _, _ = categorize_merchant(merchant_match_text(best))
            pairs.append((refund["id"], best["id"], float(refund["amount"]), category or "Uncategorized", subcategory))
    return pairs


def link_one_off_inflows(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            nt.id, nt.transaction_date, nt.amount, nt.direction, nt.counterparty_name,
            nt.counterparty_account_hash, nt.description, nt.normalized_merchant, nt.reference,
            a.display_name AS account_name, a.institution,
            ta.economic_class, ta.category, ta.subcategory, ta.confidence, ta.explanation
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount >= ?
        ORDER BY nt.transaction_date, nt.id
        """,
        (ONE_OFF_INFLOW_REVIEW_THRESHOLD,),
    ).fetchall()
    for row in rows:
        tx = dict(row)
        text = signal_text(tx)
        if known_structural_inflow(tx) and tx["economic_class"] != "refund":
            continue
        linked = False
        if any(keyword in text for keyword in SUBSIDY_LINK_KEYWORDS):
            linked = link_subsidy_offset(conn, tx)
        if linked:
            continue
        if known_structural_inflow(tx):
            continue
        mark_one_off_inflow_for_review(conn, tx)


def known_structural_inflow(tx: Dict) -> bool:
    if tx["economic_class"] in {"refund", "reimbursement_pass_through", "wealth_allocation", "internal_transfer"}:
        return True
    category = tx.get("category") or ""
    subcategory = tx.get("subcategory") or ""
    if category == "Interest":
        return True
    if category == "Income" and subcategory in {"Salary", "Cash Bonus"}:
        return True
    if category == "Equity Compensation" and subcategory == "RSU":
        return True
    if category == "Benefits" and subcategory == "Child Benefit":
        return True
    return False


def link_subsidy_offset(conn: sqlite3.Connection, inflow: Dict) -> bool:
    inflow_amount = float(inflow["amount"])
    inflow_date = parse_iso_date(inflow["transaction_date"])
    candidates = conn.execute(
        """
        SELECT
            nt.id, nt.transaction_date, nt.amount, nt.description, nt.normalized_merchant,
            ta.category, ta.subcategory
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ABS(nt.amount) >= ?
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND COALESCE(ta.category, '') NOT IN ('', 'Uncategorized', 'Unknown Card Spend')
        """,
        (inflow_amount * 3.0,),
    ).fetchall()
    best = None
    best_days = 366
    for row in candidates:
        candidate_date = parse_iso_date(row["transaction_date"])
        days = abs((candidate_date - inflow_date).days)
        if days > 365 or days >= best_days:
            continue
        best = row
        best_days = days
    if not best:
        return False

    conn.execute(
        """
        INSERT OR IGNORE INTO transaction_links (
            link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
        ) VALUES ('subsidy_offset', ?, ?, ?, 0.93, ?)
        """,
        (
            inflow["id"],
            best["id"],
            inflow_amount,
            "Linked one-off subsidy/claim inflow to a related large household outflow",
        ),
    )
    conn.execute(
        """
        UPDATE transaction_annotations
        SET economic_class = 'refund',
            category = ?,
            subcategory = ?,
            confidence = 0.93,
            digest_tier = 'auto_silent',
            explanation = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE transaction_id = ?
        """,
        (
            best["category"] or "Uncategorized",
            best["subcategory"] or "",
            f"Linked one-off subsidy/claim to transaction {best['id']} ({best['normalized_merchant'] or best['description'] or 'large outflow'})",
            inflow["id"],
        ),
    )
    return True


def mark_one_off_inflow_for_review(conn: sqlite3.Connection, tx: Dict) -> None:
    if float(tx["amount"]) < ONE_OFF_INFLOW_REVIEW_THRESHOLD:
        return
    if tx["economic_class"] not in {"income", "needs_review"}:
        return
    conn.execute(
        """
        UPDATE transaction_annotations
        SET digest_tier = 'review',
            confidence = MIN(confidence, 0.69),
            explanation = COALESCE(explanation, '') || '; one-off inflow above review threshold needs a source/link',
            updated_at = CURRENT_TIMESTAMP
        WHERE transaction_id = ?
        """,
        (tx["id"],),
    )


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
    text = signal_text(tx)
    merchant_text = merchant_match_text(tx)
    transaction_id = conditions.get("transaction_id")
    account_id = conditions.get("account_id")
    counterparty_account_hash = conditions.get("counterparty_account_hash")
    direct_debit_id = str(conditions.get("direct_debit_id", "")).upper()
    merchant_contains = str(conditions.get("merchant_contains", "")).upper()
    description_contains = str(conditions.get("description_contains", "")).upper()
    min_amount = conditions.get("min_abs_amount")
    exact_abs_amount = conditions.get("abs_amount")
    amount_tolerance = conditions.get("amount_tolerance")
    amount_tolerance_pct = conditions.get("amount_tolerance_pct")
    account_role = conditions.get("account_role")
    direction = conditions.get("direction")
    months = conditions.get("months")
    if transaction_id is not None and int(transaction_id) != int(tx["id"]):
        return False
    if account_id is not None and int(account_id) != int(tx["account_id"]):
        return False
    if counterparty_account_hash and counterparty_account_hash != tx.get("counterparty_account_hash"):
        return False
    if direct_debit_id and direct_debit_id != extract_direct_debit_id(tx):
        return False
    if merchant_contains and merchant_contains not in merchant_text:
        return False
    if description_contains and description_contains not in text:
        return False
    if min_amount is not None and abs(float(tx["amount"])) < float(min_amount):
        return False
    if exact_abs_amount is not None:
        expected = float(exact_abs_amount)
        tolerance = float(amount_tolerance or 0.0)
        if amount_tolerance_pct is not None:
            tolerance = max(tolerance, expected * float(amount_tolerance_pct))
        if abs(abs(float(tx["amount"])) - expected) > tolerance:
            return False
    if account_role and tx["account_role"] != account_role:
        return False
    if direction and tx["direction"] != direction:
        return False
    if months is not None and int(str(tx["transaction_date"])[5:7]) not in {int(month) for month in months}:
        return False
    return True


def rule_is_safely_scoped(conditions: Dict) -> bool:
    if conditions.get("transaction_id") is not None:
        return True
    if conditions.get("account_id") is not None:
        return True
    if conditions.get("counterparty_account_hash"):
        return True
    if conditions.get("direct_debit_id") and conditions.get("abs_amount") is not None:
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


def merchant_is_investment_scope(merchant: str, text: str = "") -> bool:
    scope_text = f"{merchant} {text}".upper()
    return any(keyword in scope_text for keyword in INVESTMENT_KEYWORDS)


def account_is_safe_to_promote_as_investment(conn: sqlite3.Connection, tx: Dict) -> bool:
    role = tx.get("account_role")
    institution = str(tx.get("institution") or "").lower()
    account_name = str(tx.get("account_name") or "")
    account_text = f"{institution} {account_name}".upper()
    if role == "investment" or institution in {"ibkr", "degiro"}:
        return True
    if any(keyword in account_text for keyword in INVESTMENT_KEYWORDS):
        return True
    if role in {"checking", "savings", "mortgage", "credit_card_proxy"}:
        return False
    ordinary_evidence = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.account_id = ?
          AND nt.is_duplicate = 0
          AND ta.confidence >= 0.70
          AND ta.economic_class IN (
            'income', 'household_spend', 'debt_service',
            'reimbursement_pass_through', 'refund'
          )
        """,
        (tx["account_id"],),
    ).fetchone()["count"]
    return int(ordinary_evidence) == 0


def review_group_key(row: sqlite3.Row) -> Tuple:
    if float(row["amount"]) < 0:
        creditor_id = extract_direct_debit_id(dict(row))
        if creditor_id:
            return ("recurring_debit", creditor_id, recurring_amount_bucket(float(row["amount"])))
    counterparty_hash = row["counterparty_account_hash"] or ""
    if counterparty_hash:
        return ("counterparty", counterparty_hash)
    merchant = row["normalized_merchant"] or ""
    if merchant_is_safe_scope(merchant):
        return ("merchant", merchant, row["direction"])
    return ("transaction", row["id"])


def sync_observed_income_events(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            nt.id,
            nt.transaction_date,
            nt.amount,
            a.institution,
            ta.category,
            ta.subcategory
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount > 0
          AND ta.economic_class = 'income'
        """
    ).fetchall()
    for row in rows:
        event_type = income_event_type(dict(row))
        if not event_type:
            continue
        conn.execute(
            """
            INSERT INTO expected_income_events (
                month, event_type, expected_date, expected_amount, tolerance_amount,
                status, observed_transaction_id, note
            ) VALUES (?, ?, ?, ?, 0, 'observed', ?, 'Observed during classification')
            ON CONFLICT(month, event_type, expected_date) DO UPDATE SET
                status = 'observed',
                observed_transaction_id = excluded.observed_transaction_id
            """,
            (
                str(row["transaction_date"])[:7],
                event_type,
                row["transaction_date"],
                float(row["amount"]),
                row["id"],
            ),
        )


def income_event_type(row: Dict) -> str:
    category = row.get("category") or ""
    subcategory = row.get("subcategory") or ""
    institution = str(row.get("institution") or "").lower()
    if category == "Income" and subcategory == "Salary":
        if institution == "abn":
            return "salary_abn"
        if institution == "ing":
            return "salary_ing"
        return "salary"
    if category == "Income" and subcategory == "Cash Bonus":
        return "cash_bonus"
    if category == "Benefits" and subcategory == "Child Benefit":
        return "svb_child_benefit"
    if category == "Equity Compensation" and subcategory == "RSU":
        return "rsu"
    return ""


def create_expected_income_reviews(conn: sqlite3.Connection) -> None:
    events = conn.execute(
        """
        SELECT *
        FROM expected_income_events
        WHERE status = 'expected'
          AND observed_transaction_id IS NULL
        ORDER BY month, event_type, expected_date
        """
    ).fetchall()
    for event in events:
        observed = find_expected_income_observation(conn, dict(event))
        if observed:
            conn.execute(
                """
                UPDATE expected_income_events
                SET status = 'observed', observed_transaction_id = ?
                WHERE id = ?
                """,
                (observed["id"], event["id"]),
            )
            continue
        suggested = {
            "event_type": event["event_type"],
            "month": event["month"],
            "expected_date": event["expected_date"],
            "expected_amount": event["expected_amount"],
            "action": "import_or_correct_income_source",
        }
        conn.execute(
            """
            INSERT INTO review_items (
                transaction_id, expected_event_id, issue_type, materiality,
                suggested_action_json, reason, status
            ) VALUES (NULL, ?, 'missing_income_event', ?, ?, ?, 'open')
            """,
            (
                event["id"],
                abs(float(event["expected_amount"] or 0)),
                json_dumps(suggested),
                f"Expected income event not observed: {event['event_type']} for {event['month']}",
            ),
        )


def find_expected_income_observation(conn: sqlite3.Connection, event: Dict) -> Optional[sqlite3.Row]:
    expected_amount = event.get("expected_amount")
    tolerance = float(event.get("tolerance_amount") or 0)
    params: List = [event["month"], event["event_type"]]
    amount_clause = ""
    if expected_amount is not None:
        amount_clause = "AND ABS(nt.amount - ?) <= ?"
        params.extend([float(expected_amount), tolerance or max(1.0, abs(float(expected_amount)) * 0.02)])
    return conn.execute(
        f"""
        SELECT nt.id
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND substr(nt.transaction_date, 1, 7) = ?
          AND ta.economic_class = 'income'
          AND ? = CASE
              WHEN ta.category = 'Income' AND ta.subcategory = 'Salary' AND lower(a.institution) = 'abn' THEN 'salary_abn'
              WHEN ta.category = 'Income' AND ta.subcategory = 'Salary' AND lower(a.institution) = 'ing' THEN 'salary_ing'
              WHEN ta.category = 'Income' AND ta.subcategory = 'Salary' THEN 'salary'
              WHEN ta.category = 'Income' AND ta.subcategory = 'Cash Bonus' THEN 'cash_bonus'
              WHEN ta.category = 'Benefits' AND ta.subcategory = 'Child Benefit' THEN 'svb_child_benefit'
              WHEN ta.category = 'Equity Compensation' AND ta.subcategory = 'RSU' THEN 'rsu'
              ELSE ''
          END
          {amount_clause}
        ORDER BY ABS(julianday(nt.transaction_date) - julianday(COALESCE(?, nt.transaction_date)))
        LIMIT 1
        """,
        tuple(params + [event.get("expected_date")]),
    ).fetchone()


def create_review_items(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            nt.id, nt.amount, nt.direction, nt.normalized_merchant, nt.counterparty_account_hash,
            nt.description, nt.counterparty_name,
            ta.economic_class, ta.confidence, ta.digest_tier, ta.explanation
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND (
            ta.economic_class = 'needs_review'
            OR ta.digest_tier = 'review'
            OR ta.confidence < 0.55
            OR (ta.category = 'Uncategorized' AND ABS(nt.amount) >= 100)
          )
        ORDER BY ABS(nt.amount) DESC
        """
    ).fetchall()

    grouped: Dict[Tuple, Dict] = {}
    for row in rows:
        key = review_group_key(row)
        amount = abs(float(row["amount"]))
        group = grouped.get(key)
        if not group:
            grouped[key] = {
                "row": row,
                "count": 1,
                "materiality": amount,
                "max_amount": amount,
            }
            continue
        group["count"] += 1
        group["materiality"] += amount
        if amount > group["max_amount"]:
            group["row"] = row
            group["max_amount"] = amount

    review_groups = sorted(grouped.values(), key=lambda item: item["materiality"], reverse=True)
    for group in review_groups[:MAX_OPEN_REVIEW_GROUPS]:
        row = group["row"]
        amount = float(group["materiality"])
        issue_type = "classification"
        if row["economic_class"] == "needs_review":
            issue_type = "needs_review"
        suggested = {
            "economic_class": "household_spend" if float(row["amount"]) < 0 else "income",
            "category": "Uncategorized",
            "create_rule": bool(row["normalized_merchant"] or row["counterparty_account_hash"]),
            "group_count": group["count"],
            "group_materiality": round(amount, 2),
        }
        if extract_direct_debit_id(dict(row)):
            suggested["create_rule"] = True
            suggested["scope"] = "recurring_direct_debit"
        reason = row["explanation"]
        if group["count"] > 1:
            reason = f"{reason}; grouped {group['count']} similar transactions"
        conn.execute(
            """
            INSERT INTO review_items (
                transaction_id, issue_type, materiality, suggested_action_json, reason, status
            ) VALUES (?, ?, ?, ?, ?, 'open')
            """,
            (row["id"], issue_type, amount, json_dumps(suggested), reason),
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
        """
        SELECT
            nt.id, nt.account_id, nt.counterparty_account_hash, nt.normalized_merchant,
            nt.direction, nt.amount, nt.description, nt.counterparty_name,
            a.role AS account_role, a.institution, a.display_name AS account_name
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        WHERE nt.id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    tx_dict = dict(tx)
    merchant = tx["normalized_merchant"] or ""
    counterparty_hash = tx["counterparty_account_hash"] or ""
    text = signal_text(tx_dict)
    name_scope = merchant or "transaction"
    recurring_conditions = recurring_debit_rule_conditions(tx_dict)

    if economic_class == "wealth_allocation" and counterparty_hash:
        conditions = {"counterparty_account_hash": counterparty_hash, "direction": tx["direction"]}
        name_scope = "matching counterparty account"
    elif recurring_conditions and economic_class in {"debt_service", "household_spend"}:
        conditions = recurring_conditions
        name_scope = "recurring direct debit"
    elif counterparty_hash and not merchant_is_safe_scope(merchant):
        conditions = {"counterparty_account_hash": counterparty_hash, "direction": tx["direction"]}
        name_scope = "matching counterparty account"
    elif economic_class == "wealth_allocation" and account_is_safe_to_promote_as_investment(conn, tx_dict):
        conditions = {"account_id": tx["account_id"]}
        name_scope = f"{tx['account_name']} account"
        if tx["account_role"] != "investment":
            conn.execute("UPDATE accounts SET role = 'investment' WHERE id = ?", (tx["account_id"],))
    elif (
        economic_class == "wealth_allocation"
        and merchant_scope
        and merchant_is_safe_scope(merchant)
        and merchant_is_investment_scope(merchant, text)
    ):
        conditions = {"merchant_contains": merchant, "direction": tx["direction"]}
    elif economic_class in RISKY_REVIEW_CLASSES and counterparty_hash:
        conditions = {"counterparty_account_hash": counterparty_hash, "direction": tx["direction"]}
        name_scope = "matching counterparty account"
    elif economic_class in RISKY_REVIEW_CLASSES or not (merchant_scope and merchant_is_safe_scope(merchant)):
        conditions = {"transaction_id": transaction_id}
        name_scope = f"transaction {transaction_id}"
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
        (f"Classify {name_scope} as {category}", json_dumps(conditions), json_dumps(actions)),
    )
    return int(cursor.lastrowid)


def recurring_debit_rule_conditions(tx: Dict) -> Optional[Dict]:
    amount = float(tx.get("amount") or 0)
    if amount >= 0:
        return None
    signature_type, signature_value = recurring_signature(tx)
    if signature_type != "direct_debit_id" or not signature_value:
        return None
    conditions = {
        "direction": tx.get("direction") or "outflow",
        "abs_amount": round(abs(amount), 2),
        "amount_tolerance_pct": RECURRING_DIRECT_DEBIT_AMOUNT_TOLERANCE_PCT,
    }
    conditions[signature_type] = signature_value
    return conditions
