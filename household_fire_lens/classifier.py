from __future__ import annotations

import calendar
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    (("ALBERT HEIJN", "AH TO GO", "JUMBO", "LIDL", "ALDI", "PLUS SUPERMARKT", "DIRK", "VOMAR", "DEKAMARKT", "HOOGVLIET", "NATUURLIJKBESTELD", "HAARLEM TEA"), "Groceries", ""),
    (("RESTAURANT", "CAFE", "BAR ", "UBER EATS", "DELIVEROO", "THUISBEZORGD", "MCDONALD", "BURGER", "PIZZA", "TROUBADOUR", "SUSHI", "STARBUCKS", "HMSHOST", "KOFFIE", "SNACKBAR", "CAFETARIA", "EETCAFE", "BAKKERIJ", "COFFEE", "KFC", "SUBWAY", "LA PLACE", "VAPIANO", "GRILL"), "Eating Out", ""),
    (("AIRBNB", "HOTEL", "HOSTEL", "KLM", "TRANSAVIA", "RYANAIR", "EASYJET", "EXPEDIA", "TUI", "SUNWEB", "BOOKING.COM", "BOOKING COM", "VRBO", "FLIGHT", "AIRLINE", "TURKISH AIRLINES", "LANDAL", "PEGASUS", "AJET HAVA", "HOF VAN SAKSEN", "NOOITGEDACHT", "CENTER PARCS", "ROOMPOT", "RESORT"), "Holiday", ""),
    (("NS ", "NS-", "OV-CHIP", "OVPAY", "SHELL", "BP ", "ESSO", "PARKING", "Q-PARK", "UBER", "BOLT", "AUTOMOTIVE", "KWIKFIT", "OPONEO", "GARAGE", "AUTO BEDRIJF", "AUTOBEDR", "FIETSVOORDEEL", "YELLOWBRICK", "SMOOV", "ALLEGO", "FASTNED", "TINQ", "TANGO", "SHELL RECHARGE", "GVB", "RET ", "ARRIVA", "CONNEXXION", "FLIXBUS", "GREENWHEELS"), "Transportation", ""),
    (("SPOTIFY", "NETFLIX", "APPLE.COM/BILL", "GOOGLE", "ICLOUD", "PATREON", "SUBSCRIPTION", "OPENAI", "CHATGPT", "DPG MEDIA"), "Subscriptions", ""),
    (("ENERGIE", "VATTENFALL", "ENECO", "WATER", "INTERNET", "ZIGGO", "KPN", "ODIDO", "ETECK", "POWERPEERS", "VANDEBRON", "FRANK ENERGIE"), "Housing", "Utilities"),
    (("INSURANCE", "VERZEKERING", "ALLIANZ", "AON", "ASR", "NN ", "NATIONALE NED", "TAF BV", "RISK VERZEKERINGEN"), "Housing", "Insurance"),
    (("MEUBEL", "HENDERS EN HAZEL", "DE RUIJTER MEUBEL", "KEUKENLOODS", "PRAXIS", "GAMMA", "KARWEI", "FURNITURE", "HOME IMPROVEMENT", "RUITER DAKKAPELLEN", "SOLAR TOTAAL", "STUKADOOR", "VUGTS ZONWERING", "MOOI MAKELAARDIJ"), "Home and Furniture", "Home Improvement"),
    (("HAPPY VALLEY", "DIERENPENSION", "DIERENARTS", "DIERENARTSPRAKTIJK", "VETERINARY", "VET ", " HOND ", "HOND TOBY"), "Pet Care", ""),
    (("APOTHEEK", "PHARMACY", "HOSPITAL", "ZORG", "DENTIST", "TANDARTS", "EYE WISH", "OPTICIAN", "KRUIDVAT", "ETOS", "TREKPLEISTER", "SPORTCITY", "HEALTH CLUB", "HEALTH RACQUET", "RACQUET", "PADEL", "TENNIS", "FITNESS", "BASIC-FIT", "BASIC FIT", "TRAINMORE", "AMSTELHOF SPORT", "FYSIO", "HUISARTS"), "Health", ""),
    (("AMAZON", "BOL.COM", "IKEA", "H&M", "H & M", "HEMA", "C&A", "ZARA", "COOLBLUE", "MEDIA MARKT", "DECATHLON", "THEPHONELAB", "ACTION", "UNIQLO", "LIMANGO", "WEHKAMP", "WE FASHION", "BAMBULAB", "BAMBU LAB", "ARTENCRAFT", "BEEKMAN B.V.", "ONE2TRACK", "APPLE STORE", "BAX SHOP", "BAX-SHOP", "PRIMARK", "ZALANDO", "MEDIAMARKT"), "Shopping", ""),
    (("BELASTING", "TAX", "GEMEENTE", "WATERNSCHAP", "WATERSCHAP", "PUBLIEKSZAKEN"), "Taxes and Government", ""),
    (("KINDERRIJK", "CODERMINDS", "MW IS SMITS", "BCML ENTERPRISE", "I TURN", "SQUALA", "SQULA", "DANCE WAREHOUSE", "ACTIVITEITENCOMMISSIE OBS TWICKEL", "OBS TWICKEL", "BASISSCHOOL", "SCOUTING", "MONKEY MOVES", "KINDEROPVANG", "ZWEMLES", "ZWEMBAD", "GYMNASTIEK", "MUZIEKSCHOOL", "DOZZI VIA TIKKIE"), "Education", "Kids Activities"),
    (("EMERITUS", "MIT XPRO", "CAMBRIDGE"), "Education", "Professional Education"),
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
    "BITVAVO",
)
MORTGAGE_KEYWORDS = ("MORTGAGE", "HYPOTHEEK", "HYPOTHECAIR", "HYPOTHEEKRENTE")
BOOKING_REIMBURSEMENT_KEYWORDS = ("BOOKING.COM", "BOOKING COM", "BOOKINGCOM", "BOOKING")
CARD_KEYWORDS = ("CREDITCARD", "CREDIT CARD", "MASTERCARD", "VISA", "ICS", "AMEX", "AMERICAN EXPRESS")
CREDIT_CARD_PAYMENT_KEYWORDS = ("HARTELIJK BEDANKT VOOR UW BETALING", "THANK YOU FOR YOUR PAYMENT", "BETALING", "PAYMENT RECEIVED", "AFLOSSING")
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
CASH_WITHDRAWAL_KEYWORDS = ("GELDMAAT", "ATM", "CASH WITHDRAWAL", "CASH MACHINE", "CONTANTOPNAME", "GELDAUTOMAAT", "ING WITHDRAWAL", "GEA NR")
CASH_DEPOSIT_KEYWORDS = ("ING DEPOSIT", "CASH DEPOSIT", "GELDSTORTING")
CARD_TERMINAL_PROCESSOR_KEYWORDS = ("ZETTLE", "SUMUP", "PAY.NL", "STICHTING MOLLIE PAYMENTS", "MOLLIE PAYMENTS", " VIA MOLLIE", "RIVERTY", "BUCKAROO", "BUC KAROO")
PAYMENT_REQUEST_KEYWORDS = ("TIKKIE", "BETAALVERZOEK", "PAYMENT REQUEST", "BETAALVERZOEKJE")
BANK_TRANSFER_KEYWORDS = ("SEPA OVERBOEKING", "SEPA", "OVERBOEKING", "OVERSCHRIJVING", "BANK TRANSFER")
RISKY_REVIEW_CLASSES = {"wealth_allocation", "internal_transfer", "reimbursement_pass_through", "ignore_noise"}
GENERIC_MERCHANT_SCOPES = {"", "SEPA", "SEPA OVERBOEKING", "TRANSACTION", "TRANSFER", "OVERSCHRIJVING", "INCASSO"}
MAX_OPEN_REVIEW_GROUPS = 150
UNKNOWN_OUTFLOW_REVIEW_THRESHOLD = 250.0
ONE_OFF_INFLOW_REVIEW_THRESHOLD = 500.0

# Summer-holiday trip detection. The debit "Payment terminal" export format always
# carries a 3-letter country code (NLD domestic vs foreign); we seed trips from
# foreign-coded terminal payments in the holiday months, cluster them by country and
# date, then absorb nearby physical foreign card spend into one labeled trip so the
# whole holiday collapses into a single "Holiday / <Country> <Year>" bucket.
HOLIDAY_COUNTRY_CODES = {
    "ESP": "Spain", "ITA": "Italy", "FRA": "France", "GRC": "Greece", "PRT": "Portugal",
    "HRV": "Croatia", "TUR": "Turkey", "GBR": "United Kingdom", "AUT": "Austria",
    "CHE": "Switzerland", "USA": "United States", "MAR": "Morocco", "MEX": "Mexico",
    "THA": "Thailand", "IDN": "Indonesia", "EGY": "Egypt", "CYP": "Cyprus", "MLT": "Malta",
}
HOLIDAY_COUNTRY_NAMES = {
    "SPAIN": "Spain", "ITALY": "Italy", "FRANCE": "France", "GREECE": "Greece",
    "PORTUGAL": "Portugal", "CROATIA": "Croatia", "TURKEY": "Turkey", "AUSTRIA": "Austria",
    "SWITZERLAND": "Switzerland", "MOROCCO": "Morocco", "CYPRUS": "Cyprus", "MALTA": "Malta",
    "EGYPT": "Egypt", "THAILAND": "Thailand", "INDONESIA": "Indonesia",
}
HOLIDAY_DOMESTIC_MARKERS = ("NLD", "NEDERLAND")
# Dutch cities/towns whose presence marks a transaction as domestic even when the
# export omits the "NLD" country tag (common on ABN/ING terminal rows).
HOLIDAY_DOMESTIC_CITIES = (
    "AMSTERDAM", "AMSTELVEEN", "UITHOORN", "AALSMEER", "ZAANDAM", "HAARLEM", "HOOFDDORP",
    "SCHIPHOL", "DIEMEN", "DUIVENDRECHT", "BADHOEVEDORP", "OUDERKERK", "ABCOUDE",
    "MIJDRECHT", "UTRECHT", "ROTTERDAM", "DEN HAAG", "EINDHOVEN", "GRONINGEN", "ARNHEM",
    "NIJMEGEN", "TILBURG", "BREDA", "ALMERE", "ZWOLLE", "LEIDEN", "DELFT", "PURMEREND",
    "HILVERSUM", "ALKMAAR", "ZEIST", "AMERSFOORT", "APELDOORN", "ENSCHEDE", "MAASTRICHT",
    "LELYSTAD", "HAARLEMMERMEER", "VOLENDAM", "LISSE", "HEEMSTEDE", "BUSSUM", "WEESP",
)
HOLIDAY_ONLINE_MARKERS = (
    "AMAZON", "AMZN", "PAYPAL", "BOL.COM", "IDEAL", "UBER", "NETFLIX", "SPOTIFY",
    "HELP.", "AMAZON.NL", "AMAZON.DE", "MARKTPLAATS", "COOLBLUE", "ZALANDO",
)
HOLIDAY_TERMINAL_MARKERS = ("PAYMENT TERMINAL", "KAARTNUMMER", "CARD SEQUENCE", "TRANSACTIEDATUM")
HOLIDAY_TRIP_MONTHS = {6, 7, 8, 9}
HOLIDAY_TRIP_MIN_SEEDS = 3
HOLIDAY_TRIP_MAX_GAP_DAYS = 5
HOLIDAY_TRIP_WINDOW_PAD_DAYS = 3
# Generic words that must not act as a place-name link between a trip and a card line.
HOLIDAY_TOKEN_STOPWORDS = {
    "PAYMENT", "TERMINAL", "CARD", "SEQUENCE", "TRANSACTIEDATUM", "KAARTNUMMER", "VALUE",
    "DATE", "REST", "RESTAURANT", "RESTAURANTE", "SUPER", "HOTEL", "APARTHOT", "SHOP",
    "STORE", "CAFE", "PIZZERIA", "MERCADO", "SUPERMERCADO", "SUPERMARKT", "MARKET",
    "PLAYA", "PLATJA", "CALLE", "AVENIDA", "PORT", "ZONA", "BASE", "AEREA", "PUNTO",
    "TIENDA", "BOUTIQUE", "GELATERIA", "GELATS", "FARMACIA", "PARKING", "NULL",
    "SPAIN", "ITALY", "FRANCE", "GREECE", "PORTUGAL", "CROATIA", "TURKEY", "AUSTRIA",
    "SWITZERLAND", "MOROCCO", "CYPRUS", "MALTA", "EGYPT", "THAILAND", "INDONESIA",
}
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


def apply_user_resolution_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE classification_rules
        SET enabled = 0
        WHERE enabled = 1
          AND created_by = 'agent'
          AND name = 'Classify HSBC BANK PLC as Income'
          AND actions_json LIKE '%Consulting%'
        """
    )
    conn.execute(
        """
        UPDATE classification_rules
        SET enabled = 0
        WHERE enabled = 1
          AND name = 'Classify stock-plan March/April proceeds as RSU income'
          AND actions_json LIKE '%Equity Compensation%'
          AND actions_json LIKE '%RSU%'
        """
    )
    reviewed_reimbursement_rules = conn.execute(
        """
        SELECT cr.id, cr.actions_json, ta.transaction_id
        FROM classification_rules cr
        JOIN transaction_annotations ta ON ta.rule_id = cr.id
        WHERE cr.enabled = 1
          AND cr.name = 'Classify matching counterparty account as Reimbursements'
          AND cr.conditions_json LIKE '%counterparty_account_hash%'
          AND cr.actions_json LIKE '%Reimbursements%'
          AND cr.actions_json LIKE '%"subcategory": ""%'
          AND ta.review_status = 'reviewed'
        """
    ).fetchall()
    for row in reviewed_reimbursement_rules:
        actions = json_loads(row["actions_json"], {})
        actions["subcategory"] = actions.get("subcategory") or "Company Expense"
        conn.execute(
            """
            UPDATE classification_rules
            SET conditions_json = ?, actions_json = ?
            WHERE id = ?
            """,
            (
                json_dumps({"transaction_id": row["transaction_id"]}),
                json_dumps(actions),
                row["id"],
            ),
        )
    conn.execute(
        """
        UPDATE classification_rules
        SET enabled = 0
        WHERE enabled = 1
          AND name = 'Classify matching counterparty account as Reimbursements'
          AND conditions_json LIKE '%counterparty_account_hash%'
          AND actions_json LIKE '%Reimbursements%'
          AND actions_json LIKE '%"subcategory": ""%'
        """
    )
    conn.execute(
        """
        UPDATE normalized_transactions
        SET is_duplicate = 0
        WHERE is_duplicate = 1
          AND NOT EXISTS (
              SELECT 1
              FROM normalized_transactions other
              WHERE other.source_fingerprint = normalized_transactions.source_fingerprint
                AND other.is_duplicate = 0
                AND other.source_file_id != normalized_transactions.source_file_id
          )
        """
    )
    conn.execute(
        """
        DELETE FROM transaction_links
        WHERE link_type = 'duplicate'
          AND (
              from_transaction_id IN (SELECT id FROM normalized_transactions WHERE is_duplicate = 0)
              OR to_transaction_id IN (SELECT id FROM normalized_transactions WHERE is_duplicate = 0)
          )
        """
    )
    rows = conn.execute(
        """
        SELECT id, conditions_json
        FROM classification_rules
        WHERE enabled = 1
          AND name = 'Classify stock-plan March/April proceeds as RSU income'
          AND conditions_json LIKE '%"months"%'
        """
    ).fetchall()
    for row in rows:
        conditions = json_loads(row["conditions_json"], {})
        if "months" not in conditions:
            continue
        conditions.pop("months", None)
        conn.execute(
            "UPDATE classification_rules SET conditions_json = ? WHERE id = ?",
            (json_dumps(conditions), row["id"]),
        )


def classify_all(conn: sqlite3.Connection) -> Dict[str, int]:
    apply_user_resolution_migrations(conn)
    conn.execute("DELETE FROM transaction_annotations")
    conn.execute("DELETE FROM transaction_links WHERE link_type != 'duplicate'")
    conn.execute("DELETE FROM review_items WHERE status = 'open'")
    transactions = load_transactions(conn)
    salary_ids = detect_salary_ids(transactions)
    transfer_pairs = detect_transfer_pairs(transactions)
    card_settlement_pairs = detect_card_settlement_pairs(transactions)
    refund_pairs = detect_refund_pairs(transactions)
    recurring_debit_groups = detect_recurring_direct_debits(transactions)
    holiday_trips = detect_holiday_trips(transactions)

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

    linked_card_settlement_ids = set()
    for bank_id, card_id, amount, explanation in card_settlement_pairs:
        linked_card_settlement_ids.add(bank_id)
        linked_card_settlement_ids.add(card_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_links (
                link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
            ) VALUES ('card_settlement_pair', ?, ?, ?, 0.97, ?)
            """,
            (bank_id, card_id, amount, explanation),
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
            linked_card_settlement_ids=linked_card_settlement_ids,
            refund_category_by_id=refund_category_by_id,
            recurring_debit_groups=recurring_debit_groups,
            holiday_trips=holiday_trips,
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
    sync_expected_income_calendar(conn)
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
    linked_card_settlement_ids: Iterable[int],
    refund_category_by_id: Dict[int, Tuple[str, str]],
    recurring_debit_groups: Dict[int, RecurringDebitGroup],
    user_rules: List[Dict],
    entity_hints: Optional[Dict[str, EntityHint]] = None,
    holiday_trips: Optional[Dict[int, str]] = None,
) -> Annotation:
    raw_text = tx_text(tx)
    text = signal_text(tx)
    merchant_text = merchant_match_text(tx)
    amount = float(tx["amount"])
    role = tx["account_role"]
    native_currency = (tx.get("native_currency") or tx.get("currency") or "EUR").upper()
    salary_set = set(salary_ids)
    transfer_set = set(linked_transfer_ids)
    card_settlement_set = set(linked_card_settlement_ids)

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

    if is_booking_invoice_reimbursement(tx):
        return Annotation("reimbursement_pass_through", "Reimbursements", "Company Expense", 0.94, "Booking.com invoice/expense reimbursement")

    if tx["id"] in salary_set:
        if is_cash_bonus_income(tx):
            return Annotation("income", "Income", "Cash Bonus", 0.94, "February payroll bonus pattern")
        return Annotation("income", "Income", "Salary", 0.94, "Recurring salary pattern: payer/date window/amount similarity")

    if tx["id"] in card_settlement_set:
        return Annotation("internal_transfer", "Card Settlement", "", 0.97, "Matched bank/card settlement pair")

    if holiday_trips and tx["id"] in holiday_trips:
        trip = holiday_trips[tx["id"]]
        return Annotation("household_spend", "Holiday", trip, 0.8, f"Summer holiday trip abroad: {trip}")

    if role == "credit_card":
        if amount > 0 and any(keyword in text for keyword in CREDIT_CARD_PAYMENT_KEYWORDS):
            return Annotation("internal_transfer", "Card Settlement", "", 0.9, "Credit-card payment credit imported from card statement")
        if amount > 0:
            category, subcategory, _, _ = categorize_merchant(merchant_text)
            return Annotation("refund", category or "Other", subcategory, 0.76, "Credit-card refund or merchant credit")
        category, subcategory, confidence, explanation = categorize_merchant(merchant_text)
        if category:
            return Annotation("household_spend", category, subcategory or "Card Spend", confidence, explanation)
        return Annotation("household_spend", "Other", "Card Spend", 0.62, "Imported credit-card transaction")

    if role == "wise":
        if amount < 0 and "SHARES" in text:
            return Annotation("wealth_allocation", "Investments", "RSU Settlement", 0.84, "Wise RSU share-booking outflow")
        if amount < 0 and any(keyword in text for keyword in INVESTMENT_KEYWORDS):
            return Annotation("wealth_allocation", "Investments", "Wise to Broker", 0.9, "Wise transfer to broker")
        if is_wise_rsu_inflow(tx):
            return Annotation("income", "Equity Compensation", "RSU", 0.88, "Wise USD stock-plan proceeds in vest window")
        if amount > 0:
            return Annotation("internal_transfer", "Inter-account Transfers", "Wise", 0.84, "Wise top-up from own account")
        if amount < 0:
            return Annotation("household_spend", "Other", "Personal Transfer", 0.66, "Wise outbound person-to-person transfer")

    if role == "savings":
        if any(keyword in text for keyword in CURRENT_ACCOUNT_TRANSFER_KEYWORDS):
            return Annotation("internal_transfer", "Inter-account Transfers", "Savings", 0.92, "Savings account transfer to or from current account")
        if amount > 0 and "INTEREST" in text:
            return Annotation("income", "Interest", "Savings", 0.9, "Savings account interest received")

    if amount > 0 and any(keyword in text for keyword in CASH_DEPOSIT_KEYWORDS):
        return Annotation("internal_transfer", "Cash Deposit", "", 0.82, "Cash deposited into bank account")

    if amount > 0 and "DEPOTBETALING" in text:
        return Annotation("refund", "Home and Furniture", "Home Improvement", 0.82, "Mortgage/home-improvement depot payment")

    if any(keyword in text for keyword in ("TRANSFERWISE", "WISE EUROPE", "WISE PAYMENTS")) or "WISE" in merchant_text.split():
        return Annotation("internal_transfer", "Inter-account Transfers", "Wise", 0.84, "Wise/TransferWise own-account bridge")

    if amount > 0 and any(keyword in text for keyword in SOCIAL_INSURANCE_KEYWORDS):
        if any(keyword in text for keyword in CHILD_BENEFIT_KEYWORDS):
            return Annotation("income", "Benefits", "Child Benefit", 0.94, "Dutch SVB child-benefit payment")
        return Annotation("income", "Benefits", "Government Benefit", 0.88, "Dutch SVB payment")

    if is_direct_rsu_compensation(tx):
        return Annotation("income", "Equity Compensation", "RSU", 0.72, "Direct USD compensation payout treated as equity compensation")

    if any(keyword in text for keyword in BOOKING_REIMBURSEMENT_KEYWORDS) and amount > 0:
        return Annotation("reimbursement_pass_through", "Reimbursements", "Booking.com", 0.93, "Booking.com reimbursement deposit")

    if tx["id"] in refund_category_by_id or (amount > 0 and any(keyword in text for keyword in REFUND_KEYWORDS)):
        category, subcategory = refund_category_by_id.get(tx["id"], ("", ""))
        if not category or category == "Uncategorized":
            category, subcategory, _, _ = categorize_merchant(merchant_text)
        if not category or category == "Uncategorized":
            category, subcategory, _, _ = categorize_merchant(text)
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
        return Annotation("household_spend", "Subscriptions", "Account Fees", 0.84, "Recurring bank package or card fee")

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
        if not category:
            category, subcategory, _, _ = categorize_merchant(text)
        if category:
            return Annotation("refund", category, subcategory, 0.7, "Positive merchant credit treated as refund")

    if amount > 0 and amount < ONE_OFF_INFLOW_REVIEW_THRESHOLD:
        return Annotation("refund", "Other", "Misc", 0.62, "Small unmatched credit treated as misc refund")

    if amount > 0:
        return Annotation("needs_review", "Uncategorized", "", 0.45, "Positive transaction is not salary or reimbursement")

    category, subcategory, confidence, explanation = categorize_merchant(merchant_text)
    if not category:
        category, subcategory, confidence, explanation = categorize_merchant(text)
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
    paypal_merchant = extract_paypal_merchant(tx)
    parts = [paypal_merchant, tx.get("counterparty_name"), tx.get("normalized_merchant")]
    return re.sub(r"\s+", " ", " ".join(str(part or "").upper() for part in parts)).strip()


def extract_paypal_merchant(tx: Dict) -> str:
    raw = " ".join(str(part or "") for part in [tx.get("counterparty_name"), tx.get("normalized_merchant"), tx.get("description")]).upper()
    if "PAYPAL" not in raw:
        return ""
    text = clean_signal_text(raw)
    match = re.search(r"PAYPAL\s+([A-Z0-9&.+ ]{3,60})", text)
    if not match:
        return ""
    merchant = match.group(1)
    merchant = re.split(r"\b(IBAN|REFERENCE|KENMERK|VALUE|DATE|EUROPE|S\.?A|SARL|ET|CIE|SCA)\b", merchant, maxsplit=1)[0]
    merchant = re.sub(r"[^A-Z0-9&.+ ]+", " ", merchant)
    return re.sub(r"\s+", " ", merchant).strip()


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


def is_booking_invoice_reimbursement(tx: Dict) -> bool:
    amount = float(tx["amount"])
    text = signal_text(tx)
    return (
        amount > 0
        and "BOOKING" in text
        and (
            "/INV/" in text
            or " INVOICE " in f" {text} "
            or "EXPENSE" in text
            or "REIMBURSE" in text
        )
        and not has_salary_keyword(tx)
    )


def is_rsu_window(day: date) -> bool:
    return day.month in {3, 4} or (day.month == 5 and day.day <= 15)


def is_wise_rsu_inflow(tx: Dict) -> bool:
    amount = float(tx["amount"])
    currencies = {
        str(tx.get("native_currency") or "").upper(),
        str(tx.get("source_currency") or "").upper(),
        str(tx.get("target_currency") or "").upper(),
        str(tx.get("currency") or "").upper(),
    }
    text = signal_text(tx)
    return (
        amount >= 1000
        and "USD" in currencies
        and "MONEY ADDED" in text
        and is_rsu_window(parse_iso_date(tx["transaction_date"]))
    )


def is_direct_rsu_compensation(tx: Dict) -> bool:
    amount = float(tx["amount"])
    text = signal_text(tx)
    currencies = {
        str(tx.get("native_currency") or "").upper(),
        str(tx.get("source_currency") or "").upper(),
        str(tx.get("target_currency") or "").upper(),
        str(tx.get("currency") or "").upper(),
    }
    has_usd_signal = "USD" in currencies or "USD" in text or "OORSPR." in text
    return amount >= 1000 and amount > 0 and has_usd_signal and "COMPENSATION" in text


def detect_salary_ids(transactions: List[Dict]) -> set:
    by_source: Dict[str, List[Dict]] = defaultdict(list)
    for tx in transactions:
        if tx["is_duplicate"] or float(tx["amount"]) <= 0:
            continue
        if not is_salary_eligible_account(tx):
            continue
        source_key = salary_source_key(tx)
        tx_date = parse_iso_date(tx["transaction_date"])
        if has_salary_keyword(tx):
            by_source[source_key].append(tx)
        elif is_salary_window(tx_date) and float(tx["amount"]) >= 1500 and not is_transfer_like_income_candidate(tx):
            by_source[source_key].append(tx)

    salary_ids = set()
    for _source_key, items in by_source.items():
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


def salary_source_key(tx: Dict) -> str:
    institution = str(tx.get("institution") or "").lower()
    counterparty_hash = tx.get("counterparty_account_hash") or ""
    if counterparty_hash:
        return f"{institution}:counterparty:{counterparty_hash}"
    merchant = str(tx.get("normalized_merchant") or tx.get("counterparty_name") or "UNKNOWN").upper()
    merchant = re.sub(r"\b\d{1,8}(?:/\d{4,6})?\b", " ", merchant)
    merchant = re.sub(r"\s+", " ", merchant).strip()
    return f"{institution}:merchant:{merchant}"


def is_salary_eligible_account(tx: Dict) -> bool:
    role = tx.get("account_role")
    institution = str(tx.get("institution") or "").lower()
    if role != "checking":
        return False
    return institution in {"ing", "abn", "generic"}


def detect_transfer_pairs(transactions: List[Dict]) -> List[Tuple[int, int, float, str, str]]:
    candidates = []
    active = [tx for tx in transactions if not tx["is_duplicate"]]
    by_amount_day: Dict[Tuple[int, int], List[Dict]] = defaultdict(list)
    tx_dates: Dict[int, date] = {}
    for tx in active:
        amount = float(tx["amount"])
        if amount == 0:
            continue
        tx_date = parse_iso_date(tx["transaction_date"])
        tx_dates[tx["id"]] = tx_date
        by_amount_day[(int(round(abs(amount) * 100)), tx_date.toordinal())].append(tx)
    for left in active:
        left_amount = float(left["amount"])
        if left_amount == 0:
            continue
        left_date = tx_dates[left["id"]]
        amount_key = int(round(abs(left_amount) * 100))
        for day_offset in range(-3, 4):
            day_candidates = by_amount_day.get((amount_key, left_date.toordinal() + day_offset), [])
            for right in day_candidates:
                if right["id"] <= left["id"]:
                    continue
                right_amount = float(right["amount"])
                if left["account_id"] == right["account_id"]:
                    continue
                if left_amount * right_amount >= 0:
                    continue
                if abs(left_amount + right_amount) > 0.01:
                    continue
                right_date = tx_dates[right["id"]]
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
                candidates.append(
                    (
                        abs((left_date - right_date).days),
                        transfer_pair_priority(left, right),
                        -abs(left_amount),
                        left["id"],
                        right["id"],
                        abs(left_amount),
                        kind,
                        explanation,
                    )
                )
    pairs = []
    used_ids = set()
    for _days, _priority, _negative_amount, left_id, right_id, amount, kind, explanation in sorted(candidates):
        if left_id in used_ids or right_id in used_ids:
            continue
        used_ids.add(left_id)
        used_ids.add(right_id)
        pairs.append((left_id, right_id, amount, kind, explanation))
    return pairs


def transfer_pair_priority(left: Dict, right: Dict) -> int:
    text = f"{signal_text(left)} {signal_text(right)}"
    if any(keyword in text for keyword in ("TERUGSTORTING", "FLATEX WITHDRAWAL", "PROCESSED FLATEX WITHDRAWAL")):
        return 0
    return 1


def detect_card_settlement_pairs(transactions: List[Dict]) -> List[Tuple[int, int, float, str]]:
    pairs = []
    active = [tx for tx in transactions if not tx["is_duplicate"]]
    bank_candidates = [
        tx
        for tx in active
        if tx["account_role"] in {"checking", "savings"}
        and float(tx["amount"]) < 0
        and any(keyword in signal_text(tx) for keyword in CARD_KEYWORDS)
    ]
    card_candidates = [
        tx
        for tx in active
        if tx["account_role"] == "credit_card"
        and float(tx["amount"]) > 0
        and any(keyword in signal_text(tx) for keyword in CREDIT_CARD_PAYMENT_KEYWORDS)
    ]
    used_cards = set()
    for bank in bank_candidates:
        bank_amount = abs(float(bank["amount"]))
        bank_date = parse_iso_date(bank["transaction_date"])
        best = None
        best_days = 999
        for card in card_candidates:
            if card["id"] in used_cards:
                continue
            if abs(float(card["amount"]) - bank_amount) > 0.01:
                continue
            days = abs((parse_iso_date(card["transaction_date"]) - bank_date).days)
            if days > 7 or days >= best_days:
                continue
            best = card
            best_days = days
        if not best:
            continue
        used_cards.add(best["id"])
        pairs.append(
            (
                bank["id"],
                best["id"],
                bank_amount,
                "Matched bank credit-card settlement to imported statement payment",
            )
        )
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


def holiday_country(tx: Dict) -> str:
    """Return the foreign country a transaction happened in, else ''.

    Two reliable signals: a 3-letter country code on the debit "Payment terminal"
    export, or a spelled-out foreign country name on the ABN world/debit export.
    """
    text = tx_text(tx)
    if "PAYMENT TERMINAL" in text:
        for code, country in HOLIDAY_COUNTRY_CODES.items():
            if re.search(rf"\b{code}\b", text):
                return country
    for name, country in HOLIDAY_COUNTRY_NAMES.items():
        if re.search(rf"\b{name}\b", text):
            return country
    return ""


def is_holiday_domestic(text: str) -> bool:
    if any(marker in text for marker in HOLIDAY_DOMESTIC_MARKERS):
        return True
    return any(re.search(rf"\b{re.escape(city)}\b", text) for city in HOLIDAY_DOMESTIC_CITIES)


def detect_holiday_trips(transactions: List[Dict]) -> Dict[int, str]:
    # Seeds are transactions with an explicit foreign country (code or name) in a
    # holiday month; they cluster into trips. City-only card lines (a credit-card row
    # that just says "CALA DOR") are absorbed when they fall in a trip window and are
    # not domestic/online/cash -- domestic terminal rows that omit "NLD" are ruled out
    # by the Dutch-city list so a Vomar-Amstelveen row never joins a Spain trip.
    seeds: List[Tuple[date, str]] = []
    for tx in transactions:
        if tx["is_duplicate"] or float(tx["amount"]) >= 0:
            continue
        country = holiday_country(tx)
        if not country:
            continue
        tx_date = parse_iso_date(tx["transaction_date"])
        if tx_date.month not in HOLIDAY_TRIP_MONTHS:
            continue
        seeds.append((tx_date, country))

    seeds.sort(key=lambda seed: (seed[1], seed[0]))
    trips: List[Dict] = []
    current: Optional[Dict] = None
    for tx_date, country in seeds:
        if (
            current
            and country == current["country"]
            and (tx_date - current["end"]).days <= HOLIDAY_TRIP_MAX_GAP_DAYS
        ):
            current["end"] = tx_date
            current["count"] += 1
        else:
            if current and current["count"] >= HOLIDAY_TRIP_MIN_SEEDS:
                trips.append(current)
            current = {"country": country, "start": tx_date, "end": tx_date, "count": 1}
    if current and current["count"] >= HOLIDAY_TRIP_MIN_SEEDS:
        trips.append(current)
    for trip in trips:
        trip["label"] = f"{trip['country']} {trip['start'].year}"

    result: Dict[int, str] = {}
    pad = timedelta(days=HOLIDAY_TRIP_WINDOW_PAD_DAYS)
    for tx in transactions:
        if tx["is_duplicate"] or float(tx["amount"]) >= 0:
            continue
        tx_date = parse_iso_date(tx["transaction_date"])
        own_country = holiday_country(tx)
        text = tx_text(tx)
        for trip in trips:
            if not (trip["start"] - pad <= tx_date <= trip["end"] + pad):
                continue
            if own_country == trip["country"]:
                result[tx["id"]] = trip["label"]
                break
            if is_holiday_domestic(text):
                continue
            if any(marker in text for marker in HOLIDAY_ONLINE_MARKERS):
                continue
            if any(marker in text for marker in CASH_WITHDRAWAL_KEYWORDS):
                continue
            if any(marker in text for marker in HOLIDAY_TERMINAL_MARKERS):
                result[tx["id"]] = trip["label"]
                break
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
        if not protected_income_for_amount_link(tx) and link_expense_reimbursement(conn, tx):
            continue
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


def protected_income_for_amount_link(tx: Dict) -> bool:
    if tx.get("economic_class") in {"wealth_allocation", "internal_transfer", "refund", "ignore_noise"}:
        return True
    category = tx.get("category") or ""
    subcategory = tx.get("subcategory") or ""
    if category == "Income" and subcategory in {"Salary", "Cash Bonus"}:
        return True
    if category == "Equity Compensation" and subcategory == "RSU":
        return True
    if category == "Benefits" and subcategory == "Child Benefit":
        return True
    if category == "Interest":
        return True
    text = signal_text(tx)
    return any(keyword in text for keyword in SALARY_KEYWORDS + BONUS_KEYWORDS)


def link_expense_reimbursement(conn: sqlite3.Connection, inflow: Dict) -> bool:
    inflow_amount = float(inflow["amount"])
    inflow_date = parse_iso_date(inflow["transaction_date"])
    start_date = date.fromordinal(inflow_date.toordinal() - 60).isoformat()
    candidates = conn.execute(
        """
        SELECT
            nt.id, nt.transaction_date, nt.amount, nt.description, nt.normalized_merchant,
            ta.category, ta.subcategory,
            ABS(ABS(nt.amount) - ?) AS amount_delta
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND ABS(ABS(nt.amount) - ?) <= MAX(1.0, ? * 0.02)
          AND nt.transaction_date <= ?
          AND nt.transaction_date >= ?
          AND NOT EXISTS (
            SELECT 1 FROM transaction_links tl
            WHERE tl.link_type = 'expense_reimbursement'
              AND (tl.from_transaction_id = nt.id OR tl.to_transaction_id = nt.id)
          )
        ORDER BY amount_delta ASC, nt.transaction_date DESC, nt.id
        LIMIT 1
        """,
        (inflow_amount, inflow_amount, inflow_amount, inflow["transaction_date"], start_date),
    ).fetchall()
    best = None
    for row in candidates:
        candidate_date = parse_iso_date(row["transaction_date"])
        if 0 <= (inflow_date - candidate_date).days <= 60:
            best = row
            break
    if not best:
        return False

    confidence = 0.98 if float(best["amount_delta"] or 0) < 0.01 else 0.92
    conn.execute(
        """
        INSERT OR IGNORE INTO transaction_links (
            link_type, from_transaction_id, to_transaction_id, amount, confidence, explanation
        ) VALUES ('expense_reimbursement', ?, ?, ?, ?, ?)
        """,
        (
            inflow["id"],
            best["id"],
            min(inflow_amount, abs(float(best["amount"]))),
            confidence,
            f"Matched one-off inflow to prior outflow {best['id']} by amount within 60 days",
        ),
    )
    conn.execute(
        """
        UPDATE transaction_annotations
        SET economic_class = 'reimbursement_pass_through',
            category = 'Reimbursements',
            subcategory = 'Expense Offset',
            confidence = ?,
            digest_tier = 'auto_visible',
            explanation = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE transaction_id = ?
        """,
        (
            confidence,
            f"Matched reimbursement to transaction {best['id']} ({best['normalized_merchant'] or best['description'] or 'prior outflow'})",
            inflow["id"],
        ),
    )
    return True


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
    start_date = date.fromordinal(inflow_date.toordinal() - 60).isoformat()
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
        if any(keyword_matches(keyword, text) for keyword in keywords):
            matched = next(keyword for keyword in keywords if keyword_matches(keyword, text))
            return category, subcategory, 0.82, f"Merchant keyword matched: {matched}"
    return "", "", 0.0, ""


def keyword_matches(keyword: str, text: str) -> bool:
    if not keyword:
        return False
    if keyword in {"BP", "ESSO", "NN"}:
        return re.search(rf"(?<![A-Z0-9]){re.escape(keyword)}(?![A-Z0-9])", text) is not None
    return keyword in text


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
    conn.execute(
        """
        DELETE FROM expected_income_events
        WHERE note = 'Observed during classification'
           OR note LIKE 'Auto-generated from observed recurring salary cadence%'
           OR note LIKE 'Auto-generated cash-bonus expectation:%'
           OR note LIKE 'Auto-generated RSU expectation:%'
        """
    )
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


def sync_expected_income_calendar(conn: sqlite3.Connection) -> None:
    sync_expected_salary_events(conn)
    sync_expected_cash_bonus_events(conn)
    sync_expected_rsu_events(conn)


def sync_expected_salary_events(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            nt.transaction_date,
            nt.amount,
            lower(a.institution) AS institution,
            ta.subcategory
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount > 0
          AND ta.economic_class = 'income'
          AND ta.category = 'Income'
          AND ta.subcategory IN ('Salary', 'Cash Bonus')
          AND a.role = 'checking'
          AND lower(a.institution) IN ('ing', 'abn', 'generic')
        ORDER BY nt.transaction_date
        """
    ).fetchall()
    salary_totals: Dict[str, float] = defaultdict(float)
    for row in rows:
        if row["subcategory"] == "Cash Bonus":
            continue
        salary_totals[row["month"]] += float(row["amount"])
    max_month = latest_closed_transaction_month(conn)
    if not max_month or not salary_totals:
        return
    start_month = max(min(salary_totals), "2022-01")
    observed_amounts = [
        amount
        for month, amount in salary_totals.items()
        if month >= start_month and month[5:7] != "02"
    ]
    if len(observed_amounts) < 2:
        return
    expected_amount = median(observed_amounts)
    tolerance = max(750.0, expected_amount * 0.18)
    for month in iter_months(start_month, max_month):
        if int(month[:4]) >= 2023 and month[5:7] == "02":
            continue
        expected_date = expected_salary_date(int(month[:4]), int(month[5:7]))
        conn.execute(
            """
            INSERT OR IGNORE INTO expected_income_events (
                month, event_type, expected_date, expected_amount, tolerance_amount, status, note
            ) VALUES (?, 'salary', ?, ?, ?, 'expected', ?)
            """,
            (
                month,
                expected_date,
                expected_amount,
                tolerance,
                "Auto-generated from observed recurring salary cadence; account-agnostic household total.",
            ),
        )


def sync_expected_cash_bonus_events(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            nt.transaction_date,
            nt.amount
        FROM normalized_transactions nt
        JOIN accounts a ON a.id = nt.account_id
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount > 0
          AND ta.economic_class = 'income'
          AND ta.category = 'Income'
          AND ta.subcategory = 'Cash Bonus'
          AND a.role = 'checking'
          AND lower(a.institution) IN ('ing', 'abn', 'generic')
          AND substr(nt.transaction_date, 6, 2) = '02'
          AND CAST(substr(nt.transaction_date, 1, 4) AS INTEGER) >= 2023
        ORDER BY nt.transaction_date
        """
    ).fetchall()
    if not rows:
        return
    max_month = latest_closed_transaction_month(conn)
    if not max_month:
        return
    observed_amounts = [float(row["amount"]) for row in rows]
    expected_amount = median(observed_amounts)
    tolerance = max(5000.0, expected_amount * 0.25)
    start_year = max(2023, min(int(row["month"][:4]) for row in rows))
    end_year = int(max_month[:4])
    for year in range(start_year, end_year + 1):
        if max_month < f"{year}-02":
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO expected_income_events (
                month, event_type, expected_date, expected_amount, tolerance_amount, status, note
            ) VALUES (?, 'cash_bonus', ?, ?, ?, 'expected', ?)
            """,
            (
                f"{year}-02",
                expected_salary_date(year, 2),
                expected_amount,
                tolerance,
                "Auto-generated cash-bonus expectation: February salary+bonus event starts in 2023.",
            ),
        )


def sync_expected_rsu_events(conn: sqlite3.Connection) -> None:
    observed_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM transaction_annotations
        WHERE economic_class = 'income'
          AND category = 'Equity Compensation'
          AND subcategory = 'RSU'
        """
    ).fetchone()["count"]
    if not observed_count:
        return
    bounds = conn.execute(
        """
        SELECT MIN(transaction_date) AS min_date, MAX(transaction_date) AS max_date
        FROM normalized_transactions
        WHERE is_duplicate = 0
        """
    ).fetchone()
    if not bounds or not bounds["max_date"]:
        return
    start_year = max(2022, int((bounds["min_date"] or bounds["max_date"])[:4]))
    end_year = int(bounds["max_date"][:4])
    for year in range(start_year, end_year + 1):
        if bounds["max_date"] < f"{year}-04-30":
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO expected_income_events (
                month, event_type, expected_date, expected_amount, tolerance_amount, status, note
            ) VALUES (?, 'rsu', ?, NULL, NULL, 'expected', ?)
            """,
            (
                f"{year}-04",
                f"{year}-04-15",
                "Auto-generated RSU expectation: vest first week of March, proceeds land on Wise/checking between Mar 1 and May 15.",
            ),
        )


def latest_closed_transaction_month(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT MAX(transaction_date) AS max_date
        FROM normalized_transactions
        WHERE is_duplicate = 0
        """
    ).fetchone()
    if not row or not row["max_date"]:
        return ""
    latest = parse_iso_date(row["max_date"])
    if latest.day < 28:
        latest = date(latest.year - 1, 12, 1) if latest.month == 1 else date(latest.year, latest.month - 1, 1)
    return f"{latest.year:04d}-{latest.month:02d}"


def iter_months(start_month: str, end_month: str) -> Iterable[str]:
    year = int(start_month[:4])
    month = int(start_month[5:7])
    end_year = int(end_month[:4])
    end_num = int(end_month[5:7])
    while (year, month) <= (end_year, end_num):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month > 12:
            month = 1
            year += 1


def expected_salary_date(year: int, month: int) -> str:
    if month == 12:
        return f"{year}-12-21"
    payday = date(year, month, 25)
    if payday.weekday() == calendar.SATURDAY:
        day = 24
    elif payday.weekday() == calendar.SUNDAY:
        day = 26
    else:
        day = 25
    return f"{year:04d}-{month:02d}-{day:02d}"


def median(values: List[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[len(ordered) // 2]


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
                expected_event_materiality(dict(event)),
                json_dumps(suggested),
                f"Expected income event not observed: {event['event_type']} for {event['month']}",
            ),
        )


def expected_event_materiality(event: Dict) -> float:
    if event.get("expected_amount") is not None:
        return abs(float(event["expected_amount"] or 0))
    if event.get("event_type") == "rsu":
        return 1000.0
    return 1.0


def find_expected_income_observation(conn: sqlite3.Connection, event: Dict) -> Optional[sqlite3.Row]:
    if event["event_type"] == "rsu":
        year = int(event["month"][:4])
        end_date = f"{year}-12-31" if year == 2022 else f"{year}-05-15"
        return conn.execute(
            """
            SELECT nt.id
            FROM normalized_transactions nt
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.is_duplicate = 0
              AND nt.transaction_date BETWEEN ? AND ?
              AND ta.economic_class = 'income'
              AND ta.category = 'Equity Compensation'
              AND ta.subcategory = 'RSU'
            ORDER BY nt.amount DESC
            LIMIT 1
            """,
            (f"{year}-03-01", end_date),
        ).fetchone()
    if event["event_type"] == "salary":
        row = conn.execute(
            """
            SELECT MAX(nt.id) AS id, SUM(nt.amount) AS total
            FROM normalized_transactions nt
            JOIN accounts a ON a.id = nt.account_id
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.is_duplicate = 0
              AND substr(nt.transaction_date, 1, 7) = ?
              AND nt.amount > 0
              AND ta.economic_class = 'income'
              AND ta.category = 'Income'
              AND ta.subcategory = 'Salary'
              AND a.role = 'checking'
              AND lower(a.institution) IN ('ing', 'abn', 'generic')
            """,
            (event["month"],),
        ).fetchone()
        total = float(row["total"] or 0) if row else 0.0
        if total <= 0:
            return None
        expected_amount = event.get("expected_amount")
        if expected_amount is not None:
            tolerance = float(event.get("tolerance_amount") or 0)
            tolerance = tolerance or max(1.0, abs(float(expected_amount)) * 0.02)
            if total + tolerance < float(expected_amount):
                return None
        return row
    expected_amount = event.get("expected_amount")
    tolerance = float(event.get("tolerance_amount") or 0)
    params: List = [event["month"], event["event_type"]]
    amount_clause = ""
    if expected_amount is not None and not str(event["event_type"]).startswith("salary"):
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
    elif economic_class == "reimbursement_pass_through":
        conditions = {"transaction_id": transaction_id}
        name_scope = f"transaction {transaction_id}"
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
