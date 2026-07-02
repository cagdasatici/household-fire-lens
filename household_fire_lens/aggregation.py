from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Tuple


def money(value: float) -> float:
    return round(float(value or 0), 2)


def recompute_monthly_snapshots(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.execute("DELETE FROM monthly_snapshots")
    months = aggregate_months(conn)
    for month, values in months.items():
        real_income = values["real_income"]
        cashflow_burn = values["household_spend"] + values["mortgage_total"] - values["refunds"] - values["reimbursements_cleared"]
        normalized_burn = cashflow_burn + values["amortization_delta"]
        savings_rate_cashflow = ((real_income - cashflow_burn) / real_income) if real_income else None
        savings_rate_fire = ((real_income - normalized_burn) / real_income) if real_income else None
        conn.execute(
            """
            INSERT INTO monthly_snapshots (
                month, real_income, household_spend_cashflow, household_spend_normalized,
                mortgage_total, mortgage_principal_estimate, wealth_allocation, internal_transfers,
                reimbursements_received, reimbursements_cleared, refunds, net_cash_change,
                savings_rate_cashflow, savings_rate_fire
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                month,
                money(real_income),
                money(cashflow_burn),
                money(normalized_burn),
                money(values["mortgage_total"]),
                money(values["wealth_allocation"]),
                money(values["internal_transfers"]),
                money(values["reimbursements_received"]),
                money(values["reimbursements_cleared"]),
                money(values["refunds"]),
                money(values["net_cash_change"]),
                savings_rate_cashflow,
                savings_rate_fire,
            ),
        )
    conn.commit()
    return list_monthly_snapshots(conn)


def aggregate_months(conn: sqlite3.Connection) -> Dict[str, Dict[str, float]]:
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            nt.amount,
            ta.economic_class,
            ta.category
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
        ORDER BY nt.transaction_date
        """
    ).fetchall()
    months: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unknown_card_spend: Dict[str, float] = defaultdict(float)

    for row in rows:
        month = row["month"]
        amount = float(row["amount"])
        cls = row["economic_class"]
        category = row["category"] or ""
        months[month]["net_cash_change"] += amount
        if cls == "income":
            months[month]["real_income"] += max(amount, 0)
        elif cls == "household_spend":
            spend = abs(min(amount, 0))
            months[month]["household_spend"] += spend
            if category == "Unknown Card Spend":
                unknown_card_spend[month] += spend
        elif cls == "debt_service":
            months[month]["mortgage_total"] += abs(min(amount, 0))
        elif cls == "wealth_allocation":
            # Count the cash leaving checking/savings as allocation; positive investment-side rows are informational.
            if amount < 0:
                months[month]["wealth_allocation"] += abs(amount)
        elif cls == "internal_transfer":
            months[month]["internal_transfers"] += abs(amount)
        elif cls == "reimbursement_pass_through":
            months[month]["reimbursements_received"] += max(amount, 0)
        elif cls == "refund":
            months[month]["refunds"] += max(amount, 0)

    for month, values in months.items():
        values["reimbursements_cleared"] = min(values["reimbursements_received"], unknown_card_spend[month])
        values["amortization_delta"] = 0.0
    apply_amortization(conn, months)
    return months


def apply_amortization(conn: sqlite3.Connection, months: Dict[str, Dict[str, float]]) -> None:
    rows = conn.execute(
        """
        SELECT monthly_amount, start_month, end_month
        FROM amortization_rules
        WHERE review_status IN ('approved', 'auto')
        """
    ).fetchall()
    if not rows or not months:
        return
    month_keys = sorted(months)
    for rule in rows:
        for month in month_keys:
            if month < rule["start_month"]:
                continue
            if rule["end_month"] and month > rule["end_month"]:
                continue
            months[month]["amortization_delta"] += float(rule["monthly_amount"])


def list_monthly_snapshots(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM monthly_snapshots ORDER BY month").fetchall()
    return [dict(row) for row in rows]


def fire_snapshot(conn: sqlite3.Connection, fire_multiple: float = 25.0) -> Dict[str, Any]:
    snapshots = list_monthly_snapshots(conn)
    if not snapshots:
        return {
            "months": [],
            "summary": {
                "monthly_burn": 0,
                "annualized_burn": 0,
                "real_income": 0,
                "savings_rate": None,
                "wealth_allocation": 0,
                "investment_rate": None,
                "fi_number": 0,
            },
            "data_health": data_health(conn),
        }
    recent = snapshots[-12:]
    count = len(recent)
    income = sum(float(row["real_income"]) for row in recent)
    normalized = sum(float(row["household_spend_normalized"]) for row in recent)
    wealth = sum(float(row["wealth_allocation"]) for row in recent)
    monthly_burn = normalized / count if count else 0
    annualized_burn = monthly_burn * 12
    savings_rate = ((income - normalized) / income) if income else None
    investment_rate = (wealth / income) if income else None
    return {
        "months": snapshots,
        "summary": {
            "monthly_burn": money(monthly_burn),
            "annualized_burn": money(annualized_burn),
            "real_income": money(income),
            "savings_rate": savings_rate,
            "wealth_allocation": money(wealth),
            "investment_rate": investment_rate,
            "fi_number": money(annualized_burn * fire_multiple),
            "fire_multiple": fire_multiple,
        },
        "data_health": data_health(conn),
    }


def spending_breakdown(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(ta.category, 'Uncategorized') AS category,
            COALESCE(ta.subcategory, '') AS subcategory,
            ta.economic_class,
            SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow,
            SUM(CASE WHEN nt.amount > 0 THEN nt.amount ELSE 0 END) AS inflow,
            COUNT(*) AS count
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
        GROUP BY ta.economic_class, ta.category, ta.subcategory
        ORDER BY outflow DESC, inflow DESC
        """
    ).fetchall()
    return {"breakdown": [dict(row) for row in rows]}


def data_health(conn: sqlite3.Connection) -> Dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) AS count FROM normalized_transactions WHERE is_duplicate = 0").fetchone()["count"]
    annotated = conn.execute("SELECT COUNT(*) AS count FROM transaction_annotations").fetchone()["count"]
    duplicate = conn.execute("SELECT COUNT(*) AS count FROM normalized_transactions WHERE is_duplicate = 1").fetchone()["count"]
    review = conn.execute("SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'").fetchone()["count"]
    needs_review_amount = conn.execute(
        """
        SELECT COALESCE(SUM(ABS(nt.amount)), 0) AS amount
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE ta.economic_class = 'needs_review' AND nt.is_duplicate = 0
        """
    ).fetchone()["amount"]
    confidence_rows = conn.execute(
        """
        SELECT
            CASE
                WHEN confidence >= 0.85 THEN 'high'
                WHEN confidence >= 0.65 THEN 'medium'
                ELSE 'low'
            END AS bucket,
            COALESCE(SUM(ABS(nt.amount)), 0) AS amount
        FROM transaction_annotations ta
        JOIN normalized_transactions nt ON nt.id = ta.transaction_id
        WHERE nt.is_duplicate = 0
        GROUP BY bucket
        """
    ).fetchall()
    confidence = {row["bucket"]: money(row["amount"]) for row in confidence_rows}
    return {
        "transactions": total,
        "annotated": annotated,
        "duplicates": duplicate,
        "open_review_items": review,
        "needs_review_amount": money(needs_review_amount),
        "confidence_by_value": confidence,
    }
