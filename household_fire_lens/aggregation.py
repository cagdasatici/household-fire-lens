from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Tuple


CONTROLLABILITY = {
    "Eating Out": 0.9,
    "Coffee and Snacks": 0.85,
    "Shopping": 0.8,
    "Entertainment": 0.75,
    "Holiday": 0.65,
    "Travel": 0.65,
    "Education": 0.5,
    "Subscriptions": 0.65,
    "Cash Withdrawal": 0.55,
    "Home and Furniture": 0.45,
    "Groceries": 0.45,
    "Transportation": 0.4,
    "Other": 0.55,
    "Pet Care": 0.25,
    "Health": 0.2,
    "Housing": 0.15,
    "Taxes and Government": 0.05,
    "Banking and Fees": 0.5,
    "Unknown Card Spend": 0.7,
    "Uncategorized": 0.55,
}

FIXED_MERCHANT_PATTERNS = (
    "ABN AMRO BANK",
    "BELASTINGDIENST",
    "CZ",
    "DAK VOLMACHT",
    "ETECK",
    "FRANK ENERGIE",
    "GEMEENTE AMSTELVEEN",
    "KPN",
    "NN ZORG",
    "PWN",
    "RISK VERZEKERINGEN",
    "SPORTCITY",
    "TAF BV",
    "WATERSCHAP",
    "ZORG EN ZEKERHEID",
)

QUASI_FIXED_CATEGORIES = {"Groceries", "Cash Withdrawal"}


def money(value: float) -> float:
    return round(float(value or 0), 2)


def month_add(month: str, offset: int) -> str:
    year, month_num = [int(part) for part in month.split("-")]
    total = year * 12 + (month_num - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def months_between(start_month: str, end_month: str) -> Iterable[str]:
    current = start_month
    while current <= end_month:
        yield current
        current = month_add(current, 1)


def _period_years(period: str, snapshots: List[Dict[str, Any]]) -> List[str]:
    if not snapshots:
        return []
    period = (period or "last13").strip()
    all_years = sorted({str(row["month"])[:4] for row in snapshots})
    latest_year = all_years[-1]
    if period in {"all", "last13"}:
        return all_years
    if period == "ytd":
        return [latest_year]
    if period.startswith("year:"):
        year = period.split(":", 1)[1]
        return [year]
    if period.startswith("years:"):
        years = [year.strip() for year in period.split(":", 1)[1].split(",") if year.strip()]
        return years
    if period.startswith("last") and period.endswith("years"):
        digits = "".join(ch for ch in period if ch.isdigit())
        count = int(digits or "0")
        if count > 0:
            return all_years[-count:]
    if period.isdigit() and len(period) == 4:
        return [period]
    return all_years


def recompute_monthly_snapshots(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.execute("DELETE FROM monthly_snapshots")
    months = aggregate_months(conn)
    for month, values in sorted(months.items()):
        real_income = values["real_income"]
        cashflow_burn = (
            values["household_spend"]
            + values["mortgage_total"]
            - values["refunds"]
            - values["reimbursements_cleared"]
        )
        gross_outflow = values["household_spend"] + values["mortgage_total"]
        normalized_burn = cashflow_burn - values["amortized_cashflow_replaced"] + values["amortized_monthly_addition"]
        household_net_pnl = real_income - cashflow_burn
        savings_rate_cashflow = ((real_income - cashflow_burn) / real_income) if real_income else None
        savings_rate_fire = ((real_income - normalized_burn) / real_income) if real_income else None
        conn.execute(
            """
            INSERT INTO monthly_snapshots (
                month, real_income, regular_income, variable_income, household_outflow_gross,
                household_spend_cashflow, household_spend_normalized, household_net_pnl,
                mortgage_total, mortgage_principal_estimate, wealth_allocation, internal_transfers,
                reimbursements_received, reimbursements_cleared, linked_reimbursements, refunds, net_cash_change,
                savings_rate_cashflow, savings_rate_fire
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                month,
                money(real_income),
                money(values["regular_income"]),
                money(values["variable_income"]),
                money(gross_outflow),
                money(cashflow_burn),
                money(max(0, normalized_burn)),
                money(household_net_pnl),
                money(values["mortgage_total"]),
                money(values["wealth_allocation"]),
                money(values["internal_transfers"]),
                money(values["reimbursements_received"]),
                money(values["reimbursements_cleared"]),
                money(values["linked_reimbursements"]),
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
            nt.id,
            substr(nt.transaction_date, 1, 7) AS month,
            nt.amount,
            ta.economic_class,
            ta.category,
            ta.subcategory
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
            inflow = max(amount, 0)
            months[month]["real_income"] += inflow
            if category in {"Equity Compensation"} or (row["subcategory"] or "") in {"Cash Bonus", "RSU"}:
                months[month]["variable_income"] += inflow
            else:
                months[month]["regular_income"] += inflow
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

    apply_linked_reimbursements(conn, months)
    apply_reimbursement_fifo(months, unknown_card_spend)
    apply_business_reimbursement_fifo(conn, months)
    for month, values in months.items():
        values["amortized_cashflow_replaced"] = 0.0
        values["amortized_monthly_addition"] = 0.0
    apply_amortization(conn, months)
    return months


def apply_linked_reimbursements(conn: sqlite3.Connection, months: Dict[str, Dict[str, float]]) -> None:
    rows = conn.execute(
        """
        SELECT
            substr(outflow.transaction_date, 1, 7) AS month,
            substr(inflow.transaction_date, 1, 7) AS inflow_month,
            SUM(MIN(tl.amount, ABS(outflow.amount))) AS amount
        FROM transaction_links tl
        JOIN normalized_transactions outflow ON outflow.id = tl.to_transaction_id
        JOIN normalized_transactions inflow ON inflow.id = tl.from_transaction_id
        WHERE tl.link_type = 'expense_reimbursement'
        GROUP BY substr(outflow.transaction_date, 1, 7), substr(inflow.transaction_date, 1, 7)
        """
    ).fetchall()
    for row in rows:
        month = row["month"]
        amount = float(row["amount"] or 0)
        months[month]["linked_reimbursements"] += amount
        months[month]["reimbursements_cleared"] += amount
        months[row["inflow_month"]]["reimbursement_paybacks_used"] += amount


def apply_business_reimbursement_fifo(conn: sqlite3.Connection, months: Dict[str, Dict[str, float]]) -> None:
    candidate_rows = conn.execute(
        """
        SELECT substr(nt.transaction_date, 1, 7) AS month, SUM(ABS(nt.amount)) AS amount
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ta.economic_class = 'household_spend'
          AND ta.category IN ('Holiday', 'Transportation', 'Education')
          AND NOT EXISTS (
            SELECT 1 FROM transaction_links tl
            WHERE tl.link_type = 'expense_reimbursement'
              AND tl.to_transaction_id = nt.id
          )
        GROUP BY substr(nt.transaction_date, 1, 7)
        ORDER BY month
        """
    ).fetchall()
    reimbursement_rows = conn.execute(
        """
        SELECT substr(nt.transaction_date, 1, 7) AS month, SUM(nt.amount) AS amount
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount > 0
          AND ta.economic_class = 'reimbursement_pass_through'
          AND ta.subcategory IN ('Company Expense', 'Expense Offset', 'Booking.com')
          AND NOT EXISTS (
            SELECT 1 FROM transaction_links tl
            WHERE tl.link_type = 'expense_reimbursement'
              AND tl.from_transaction_id = nt.id
          )
        GROUP BY substr(nt.transaction_date, 1, 7)
        ORDER BY month
        """
    ).fetchall()
    spend_by_month = {row["month"]: float(row["amount"] or 0) for row in candidate_rows}
    reimbursement_by_month = {row["month"]: float(row["amount"] or 0) for row in reimbursement_rows}
    pending_spend: List[List[Any]] = []
    all_months = sorted(set(months) | set(spend_by_month) | set(reimbursement_by_month))
    for month in all_months:
        if spend_by_month.get(month, 0) > 0:
            pending_spend.append([month, spend_by_month[month]])
        reimbursement = max(0.0, reimbursement_by_month.get(month, 0) - months[month]["reimbursement_paybacks_used"])
        while reimbursement > 0.005 and pending_spend:
            spend_month, open_amount = pending_spend[0]
            cleared = min(reimbursement, open_amount)
            months[spend_month]["reimbursements_cleared"] += cleared
            months[spend_month]["business_reimbursements"] += cleared
            months[month]["reimbursement_paybacks_used"] += cleared
            reimbursement -= cleared
            open_amount -= cleared
            if open_amount <= 0.005:
                pending_spend.pop(0)
            else:
                pending_spend[0][1] = open_amount


def apply_reimbursement_fifo(months: Dict[str, Dict[str, float]], unknown_card_spend: Dict[str, float]) -> None:
    pending_spend: List[List[Any]] = []
    for month in sorted(months):
        if unknown_card_spend[month] > 0:
            pending_spend.append([month, unknown_card_spend[month]])

        reimbursement = max(0.0, months[month]["reimbursements_received"] - months[month]["reimbursement_paybacks_used"])
        while reimbursement > 0.005 and pending_spend:
            spend_month, open_amount = pending_spend[0]
            cleared = min(reimbursement, open_amount)
            months[spend_month]["reimbursements_cleared"] += cleared
            months[month]["reimbursement_paybacks_used"] += cleared
            reimbursement -= cleared
            open_amount -= cleared
            if open_amount <= 0.005:
                pending_spend.pop(0)
            else:
                pending_spend[0][1] = open_amount


def apply_amortization(conn: sqlite3.Connection, months: Dict[str, Dict[str, float]]) -> None:
    rows = conn.execute(
        """
        SELECT ar.*, nt.amount, substr(nt.transaction_date, 1, 7) AS transaction_month
        FROM amortization_rules ar
        LEFT JOIN normalized_transactions nt ON nt.id = ar.transaction_id
        WHERE ar.review_status IN ('approved', 'auto')
        """
    ).fetchall()
    if not rows or not months:
        return
    for rule in rows:
        start_month = rule["start_month"]
        end_month = rule["end_month"] or month_add(start_month, 11)
        for month in months_between(start_month, end_month):
            months[month]["amortized_monthly_addition"] += float(rule["monthly_amount"])
        if rule["transaction_id"] and rule["transaction_month"]:
            months[rule["transaction_month"]]["amortized_cashflow_replaced"] += abs(float(rule["amount"] or 0))


def suggest_amortization_rules(conn: sqlite3.Connection) -> None:
    """Create reviewable amortization candidates for lumpy household expenses.

    Suggestions are intentionally conservative. They never affect metrics until approved.
    """

    conn.execute(
        """
        UPDATE amortization_rules
        SET review_status = 'disabled'
        WHERE review_status = 'suggested'
          AND (
            LOWER(COALESCE(name, '')) LIKE '%mortgage%'
            OR LOWER(COALESCE(merchant_pattern, '')) LIKE '%mortgage%'
            OR category IN ('Housing', 'Banking and Fees')
            OR annual_amount < 1500
          )
        """
    )
    existing_rows = conn.execute(
        "SELECT transaction_id FROM amortization_rules WHERE transaction_id IS NOT NULL"
    ).fetchall()
    existing = {row["transaction_id"] for row in existing_rows}
    candidates = conn.execute(
        """
        SELECT
            nt.id,
            nt.transaction_date,
            nt.amount,
            nt.normalized_merchant,
            ta.category,
            ta.subcategory,
            ta.economic_class
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ABS(nt.amount) >= 1500
          AND ta.economic_class = 'household_spend'
          AND ta.category NOT IN ('Unknown Card Spend', 'Uncategorized')
          AND ta.category NOT IN ('Housing', 'Banking and Fees')
        ORDER BY ABS(nt.amount) DESC
        """
    ).fetchall()
    for row in candidates:
        if row["id"] in existing:
            continue
        merchant = row["normalized_merchant"] or row["category"] or "Annual expense"
        if recurring_monthly_merchant(conn, merchant, row["id"]):
            continue
        category = row["category"] or "Uncategorized"
        annual_amount = abs(float(row["amount"]))
        start_month = str(row["transaction_date"])[:7]
        conn.execute(
            """
            INSERT INTO amortization_rules (
                name, category, merchant_pattern, transaction_id, annual_amount,
                monthly_amount, start_month, end_month, confidence, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.72, 'suggested')
            """,
            (
                f"Amortize {merchant}",
                category,
                merchant,
                row["id"],
                money(annual_amount),
                money(annual_amount / 12),
                start_month,
                month_add(start_month, 11),
            ),
        )
    conn.commit()


def recurring_monthly_merchant(conn: sqlite3.Connection, merchant: str, transaction_id: int) -> bool:
    if not merchant:
        return False
    rows = conn.execute(
        """
        SELECT DISTINCT substr(nt.transaction_date, 1, 7) AS month
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.id != ?
          AND nt.normalized_merchant = ?
          AND nt.amount < 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
        ORDER BY month
        """,
        (transaction_id, merchant),
    ).fetchall()
    return len(rows) >= 3


def list_monthly_snapshots(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM monthly_snapshots ORDER BY month").fetchall()
    return [dict(row) for row in rows]


def assert_monthly_pnl_identity(snapshots: List[Dict[str, Any]]) -> None:
    for row in snapshots:
        income = float(row["real_income"])
        outflow = float(row["household_spend_cashflow"])
        net = float(row["household_net_pnl"])
        if abs(net - (income - outflow)) >= 0.01:
            raise AssertionError(
                f"Monthly P&L identity failed for {row['month']}: "
                f"net {net:.2f} != income {income:.2f} - outflow {outflow:.2f}"
            )


def yearly_snapshots(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in snapshots:
        year = str(row["month"])[:4]
        for key in (
            "real_income", "regular_income", "variable_income", "household_spend_cashflow",
            "household_net_pnl", "household_spend_normalized", "net_cash_change",
            "wealth_allocation", "internal_transfers", "reimbursements_cleared", "linked_reimbursements", "refunds"
        ):
            grouped[year][key] += float(row.get(key) or 0)
        grouped[year]["months"] += 1
    years = []
    for year, values in sorted(grouped.items()):
        net = values["real_income"] - values["household_spend_cashflow"]
        if abs(values["household_net_pnl"] - net) >= 0.01:
            raise AssertionError(f"Yearly P&L identity failed for {year}")
        item = {"year": year, "months": int(values["months"])}
        for key, value in values.items():
            if key != "months":
                item[key] = money(value)
        years.append(item)
    return years


def filter_snapshots_for_period(snapshots: List[Dict[str, Any]], period: str = "last13") -> List[Dict[str, Any]]:
    if not snapshots:
        return []
    period = period or "last13"
    if period == "all":
        return snapshots
    if period == "last13":
        return snapshots[-13:]
    latest_year = str(snapshots[-1]["month"])[:4]
    if period == "ytd":
        return [row for row in snapshots if str(row["month"]).startswith(latest_year)]
    if period.startswith("year:"):
        year = period.split(":", 1)[1]
        return [row for row in snapshots if str(row["month"]).startswith(year)]
    if period.startswith("years:"):
        years = {year.strip() for year in period.split(":", 1)[1].split(",") if year.strip()}
        return [row for row in snapshots if str(row["month"])[:4] in years]
    if period.startswith("last") and period.endswith("years"):
        digits = "".join(ch for ch in period if ch.isdigit())
        count = int(digits or "0")
        if count > 0:
            years = set(_period_years(period, snapshots))
            return [row for row in snapshots if str(row["month"])[:4] in years]
    if period.isdigit() and len(period) == 4:
        return [row for row in snapshots if str(row["month"]).startswith(period)]
    return snapshots[-13:]


def fire_snapshot(conn: sqlite3.Connection, fire_multiple: float = 25.0, period: str = "last13") -> Dict[str, Any]:
    all_snapshots = list_monthly_snapshots(conn)
    assert_monthly_pnl_identity(all_snapshots)
    snapshots = filter_snapshots_for_period(all_snapshots, period)
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
                "runway_months": None,
            },
            "data_health": data_health(conn),
        }
    average_snapshots = complete_average_months(snapshots)
    count = len(average_snapshots)
    income = sum(float(row["real_income"]) for row in average_snapshots)
    normalized = sum(float(row["household_spend_normalized"]) for row in average_snapshots)
    wealth = sum(float(row["wealth_allocation"]) for row in average_snapshots)
    monthly_burn = normalized / count if count else 0
    annualized_burn = monthly_burn * 12
    savings_rate = ((income - normalized) / income) if income else None
    investment_rate = (wealth / income) if income else None
    return {
        "months": snapshots,
        "all_months": all_snapshots,
        "years": yearly_snapshots(snapshots),
        "available_years": sorted({str(row["month"])[:4] for row in all_snapshots}),
        "period": period,
        "summary": {
            "monthly_burn": money(monthly_burn),
            "annualized_burn": money(annualized_burn),
            "real_income": money(income),
            "savings_rate": savings_rate,
            "wealth_allocation": money(wealth),
            "investment_rate": investment_rate,
            "fi_number": money(annualized_burn * fire_multiple),
            "fire_multiple": fire_multiple,
            "runway_months": money(wealth / monthly_burn) if monthly_burn else None,
            "average_months": count,
            "excluded_partial_month": snapshots[-1]["month"] if snapshots and snapshots[-1] not in average_snapshots else None,
            **fixed_variable_summary(conn, period),
        },
        "data_health": data_health(conn),
    }


def complete_average_months(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(snapshots) <= 1:
        return snapshots
    current_month = date.today().strftime("%Y-%m")
    if snapshots[-1]["month"] == current_month:
        return snapshots[:-1]
    return snapshots


def fixed_variable_summary(conn: sqlite3.Connection, period: str = "last13") -> Dict[str, Any]:
    rows = fixed_variable_breakdown(conn, period)
    if not rows:
        return {"fixed_floor": 0, "quasi_fixed_average": 0, "variable_average": 0}
    count = len(rows)
    return {
        "fixed_floor": money(sum(row["fixed"] for row in rows) / count),
        "quasi_fixed_average": money(sum(row["quasi_fixed"] for row in rows) / count),
        "variable_average": money(sum(row["variable"] for row in rows) / count),
    }


def fixed_variable_breakdown(conn: sqlite3.Connection, period: str = "last13") -> List[Dict[str, Any]]:
    start_month, end_month = period_bounds_from_snapshots(conn, period)
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            COALESCE(nt.normalized_merchant, nt.counterparty_name, '') AS merchant,
            COALESCE(ta.category, 'Uncategorized') AS category,
            SUM(ABS(nt.amount)) AS outflow
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY month, merchant, category
        ORDER BY month
        """,
        (start_month, end_month),
    ).fetchall()
    by_month: Dict[str, Dict[str, float]] = defaultdict(lambda: {"fixed": 0.0, "quasi_fixed": 0.0, "variable": 0.0})
    for row in rows:
        merchant = str(row["merchant"] or "").upper()
        category = row["category"] or ""
        amount = float(row["outflow"] or 0)
        if category in QUASI_FIXED_CATEGORIES:
            by_month[row["month"]]["quasi_fixed"] += amount
        elif category == "Housing" or any(pattern in merchant for pattern in FIXED_MERCHANT_PATTERNS):
            by_month[row["month"]]["fixed"] += amount
        else:
            by_month[row["month"]]["variable"] += amount
    return [
        {"month": month, **{key: money(value) for key, value in values.items()}}
        for month, values in sorted(by_month.items())
    ]


def period_bounds_from_snapshots(conn: sqlite3.Connection, period: str = "last13") -> Tuple[str, str]:
    snapshots = filter_snapshots_for_period(list_monthly_snapshots(conn), period)
    if not snapshots:
        return "0000-00", "9999-99"
    return snapshots[0]["month"], snapshots[-1]["month"]


def spending_breakdown(conn: sqlite3.Connection, period: str = "last13") -> Dict[str, Any]:
    start_month, end_month = period_bounds_from_snapshots(conn, period)
    rows = conn.execute(
        """
        SELECT
            COALESCE(ta.category, 'Uncategorized') AS category,
            COALESCE(ta.subcategory, '') AS subcategory,
            ta.economic_class,
            SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow,
            SUM(CASE WHEN nt.amount > 0 THEN nt.amount ELSE 0 END) AS inflow,
            COUNT(*) AS count,
            AVG(ta.confidence) AS avg_confidence
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY ta.economic_class, ta.category, ta.subcategory
        ORDER BY outflow DESC, inflow DESC
        """,
        (start_month, end_month),
    ).fetchall()
    category_months = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            COALESCE(ta.category, 'Uncategorized') AS category,
            SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY month, category
        ORDER BY month, category
        """,
        (start_month, end_month),
    ).fetchall()
    return {
        "breakdown": [dict(row) for row in rows],
        "category_months": [dict(row) for row in category_months],
    }


def recurring_merchants(conn: sqlite3.Connection, period: str = "last13") -> List[Dict[str, Any]]:
    start_month, end_month = period_bounds_from_snapshots(conn, period)
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(nt.normalized_merchant, ''), 'Unknown merchant') AS merchant,
            COALESCE(ta.category, 'Uncategorized') AS category,
            substr(nt.transaction_date, 1, 7) AS month,
            SUM(ABS(nt.amount)) AS amount,
            COUNT(*) AS transactions
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND nt.amount < 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY merchant, category, month
        """,
        (start_month, end_month),
    ).fetchall()
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (row["merchant"], row["category"])
        item = grouped.setdefault(
            key,
            {
                "merchant": row["merchant"],
                "category": row["category"],
                "months": [],
                "total": 0.0,
                "transactions": 0,
            },
        )
        item["months"].append({"month": row["month"], "amount": money(row["amount"])})
        item["total"] += float(row["amount"])
        item["transactions"] += int(row["transactions"])

    recurring = []
    for item in grouped.values():
        month_count = len(item["months"])
        if month_count < 2 and item["total"] < 500:
            continue
        amounts = [float(month["amount"]) for month in item["months"]]
        avg = item["total"] / max(1, month_count)
        variance = sum(abs(amount - avg) for amount in amounts) / max(1, month_count)
        stability = max(0.0, 1.0 - (variance / avg)) if avg else 0.0
        recurring.append(
            {
                "merchant": item["merchant"],
                "category": item["category"],
                "months_count": month_count,
                "monthly_average": money(avg),
                "annualized": money(avg * 12),
                "total": money(item["total"]),
                "transactions": item["transactions"],
                "stability": money(stability),
                "cadence": "monthly" if month_count >= 3 and stability >= 0.65 else "recurring",
            }
        )
    recurring.sort(key=lambda row: (row["annualized"], row["months_count"]), reverse=True)
    return recurring


def optimization_insights(conn: sqlite3.Connection, period: str = "last13") -> Dict[str, Any]:
    snapshots = filter_snapshots_for_period(list_monthly_snapshots(conn), period)
    breakdown = spending_breakdown(conn, period)["breakdown"]
    recurring = recurring_merchants(conn, period)
    category_rows = [
        row
        for row in breakdown
        if row["economic_class"] in {"household_spend", "debt_service"} and float(row["outflow"] or 0) > 0
    ]
    total_burden = sum(float(row["outflow"] or 0) for row in category_rows)
    opportunities = []
    for row in category_rows:
        category = row["category"] or "Uncategorized"
        outflow = float(row["outflow"] or 0)
        controllability = CONTROLLABILITY.get(category, 0.5)
        score = outflow * controllability
        opportunities.append(
            {
                "category": category,
                "subcategory": row["subcategory"] or "",
                "economic_class": row["economic_class"],
                "amount": money(outflow),
                "share": (outflow / total_burden) if total_burden else 0,
                "controllability": controllability,
                "score": money(score),
                "annualized_impact": money(score),
                "why": opportunity_reason(category, controllability),
            }
        )
    opportunities.sort(key=lambda row: row["score"], reverse=True)
    trend_alerts = category_trends(conn, period)
    amortization = list_amortization_rules(conn)
    return {
        "opportunities": opportunities[:8],
        "recurring": recurring[:12],
        "trend_alerts": trend_alerts[:8],
        "amortization_rules": amortization,
        "snapshot": fire_snapshot(conn, period=period),
        "summary": {
            "months_loaded": len(snapshots),
            "total_burden": money(total_burden),
            "suggested_amortizations": len([rule for rule in amortization if rule["review_status"] == "suggested"]),
            "recurring_merchants": len(recurring),
        },
    }


def category_trends(conn: sqlite3.Connection, period: str = "last13") -> List[Dict[str, Any]]:
    start_month, end_month = period_bounds_from_snapshots(conn, period)
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 7) AS month,
            COALESCE(ta.category, 'Uncategorized') AS category,
            SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY month, category
        ORDER BY month
        """,
        (start_month, end_month),
    ).fetchall()
    months = sorted({row["month"] for row in rows})
    if len(months) < 4:
        return []
    recent_months = set(months[-3:])
    prior_months = set(months[-6:-3])
    by_category: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row["month"] in recent_months:
            by_category[row["category"]]["recent"] += float(row["outflow"])
        elif row["month"] in prior_months:
            by_category[row["category"]]["prior"] += float(row["outflow"])
    alerts = []
    for category, values in by_category.items():
        recent_avg = values["recent"] / max(1, len(recent_months))
        prior_avg = values["prior"] / max(1, len(prior_months))
        if recent_avg < 75:
            continue
        change = (recent_avg - prior_avg) / prior_avg if prior_avg else 1.0
        if change < 0.15:
            continue
        alerts.append(
            {
                "category": category,
                "recent_monthly_average": money(recent_avg),
                "prior_monthly_average": money(prior_avg),
                "change": change,
                "monthly_delta": money(recent_avg - prior_avg),
            }
        )
    alerts.sort(key=lambda row: row["monthly_delta"], reverse=True)
    return alerts


def spending_insights(conn: sqlite3.Connection, period: str = "all") -> Dict[str, Any]:
    """Year-over-year spending analysis with key takeaways."""
    start_month, end_month = period_bounds_from_snapshots(conn, period)
    rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 4) AS year,
            COALESCE(ta.category, 'Uncategorized') AS category,
            SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY year, category
        ORDER BY year, category
        """
    , (start_month, end_month)).fetchall()
    month_rows = conn.execute(
        """
        SELECT
            substr(nt.transaction_date, 1, 4) AS year,
            COUNT(DISTINCT substr(nt.transaction_date, 1, 7)) AS months
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND ta.economic_class IN ('household_spend', 'debt_service')
          AND substr(nt.transaction_date, 1, 7) BETWEEN ? AND ?
        GROUP BY year
        """,
        (start_month, end_month),
    ).fetchall()
    months_by_year = {row["year"]: int(row["months"] or 0) for row in month_rows}

    by_year_category: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        by_year_category[row["year"]][row["category"]] = float(row["outflow"])

    all_categories = set()
    for year_data in by_year_category.values():
        all_categories.update(year_data.keys())

    comparison = {
        "categories": [],
        "yearly_totals": {},
        "key_takeaways": [],
    }

    for year in sorted(by_year_category.keys()):
        yearly_total = sum(by_year_category[year].values())
        months = months_by_year.get(year, 0)
        comparison["yearly_totals"][year] = {
            "total": money(yearly_total),
            "months": months,
            "monthly_average": money(yearly_total / months) if months else 0,
            "is_partial": months < 12,
        }

    for category in sorted(all_categories):
        cat_data = {
            "category": category,
            "years": {},
        }
        for year in sorted(by_year_category.keys()):
            amount = by_year_category[year].get(category, 0.0)
            cat_data["years"][year] = money(amount)
        comparison["categories"].append(cat_data)

    years = sorted(by_year_category.keys())
    for prior_year, current_year_key in zip(years, years[1:]):
        if months_by_year.get(prior_year, 0) < 12 or months_by_year.get(current_year_key, 0) < 12:
            continue
        changes = []
        for category in all_categories:
            prior_amount = by_year_category[prior_year].get(category, 0.0)
            current_amount = by_year_category[current_year_key].get(category, 0.0)
            if prior_amount <= 100:
                continue
            change_pct = ((current_amount - prior_amount) / prior_amount) * 100 if prior_amount else 0
            if abs(change_pct) <= 15:
                continue
            delta = current_amount - prior_amount
            changes.append(
                {
                    "category": category,
                    "prior_year": prior_year,
                    "current_year": current_year_key,
                    "prior_amount": money(prior_amount),
                    "current_amount": money(current_amount),
                    "change": f"{change_pct:+.1f}%",
                    "delta": money(delta),
                    "_delta_numeric": delta,
                }
            )
        changes.sort(key=lambda x: abs(x["_delta_numeric"]), reverse=True)
        for change in changes:
            del change["_delta_numeric"]
        if changes:
            comparison["key_takeaways"].append(
                {
                    "title": f"Year-over-year category shifts ({prior_year} → {current_year_key})",
                    "items": changes[:5],
                }
            )

    return comparison


def spending_story(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Return a full-history narrative for the dashboard's story panel."""
    year_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                substr(nt.transaction_date, 1, 4) AS year,
                SUM(CASE WHEN ta.economic_class IN ('household_spend', 'debt_service') AND nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS household_spend_cashflow,
                SUM(CASE WHEN ta.economic_class = 'income' AND nt.amount > 0 THEN nt.amount ELSE 0 END) AS real_income
            FROM normalized_transactions nt
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.is_duplicate = 0
            GROUP BY year
            ORDER BY year
            """
        ).fetchall()
    ]
    if not year_rows:
        return {"headline": "No story yet.", "summary": [], "events": [], "jump_drivers": []}

    yearly_lookup = {row["year"]: row for row in year_rows}
    first_year = year_rows[0]["year"]
    last_year = year_rows[-1]["year"]

    events: List[Dict[str, Any]] = []
    event_specs = [
        {
            "label": "BMW X1 purchase",
            "merchant": "AUTO BEDRIJF CENTRUM",
            "match_sql": """
                SELECT
                    nt.transaction_date,
                    nt.amount,
                    nt.description,
                    nt.normalized_merchant,
                    COALESCE(ta.category, 'Uncategorized') AS category,
                    COALESCE(ta.subcategory, '') AS subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                WHERE nt.is_duplicate = 0
                  AND (
                      upper(nt.normalized_merchant) LIKE '%AUTO BEDRIJF CENTRUM%'
                      OR nt.description LIKE '%P852DL%'
                  )
                ORDER BY ABS(nt.amount) DESC
                LIMIT 1
            """,
            "why": "The 2022 spend spike is a deliberate asset purchase, not day-to-day burn.",
        },
        {
            "label": "Heat pump installation",
            "merchant": "COMFORT PARTNERS B.V.",
            "match_sql": """
                SELECT
                    nt.transaction_date,
                    nt.amount,
                    nt.description,
                    nt.normalized_merchant,
                    COALESCE(ta.category, 'Uncategorized') AS category,
                    COALESCE(ta.subcategory, '') AS subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                WHERE nt.is_duplicate = 0
                  AND upper(nt.normalized_merchant) LIKE '%COMFORT PARTNERS%'
                ORDER BY ABS(nt.amount) DESC
                LIMIT 1
            """,
            "why": "The late-2023 jump is a home-improvement project, again a lumpy asset-style outflow.",
        },
        {
            "label": "Professional education (reimbursable)",
            "merchant": "EMERITUS MIT XPRO CAMBRIDGE",
            "match_sql": """
                SELECT
                    nt.transaction_date,
                    nt.amount,
                    nt.description,
                    nt.normalized_merchant,
                    COALESCE(ta.category, 'Uncategorized') AS category,
                    COALESCE(ta.subcategory, '') AS subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                WHERE nt.is_duplicate = 0
                  AND upper(nt.normalized_merchant) LIKE '%EMERITUS MIT XPRO CAMBRIDGE%'
                ORDER BY ABS(nt.amount) DESC
                LIMIT 1
            """,
            "why": "This is a reimbursed professional-education event from Booking: it belongs on the dashboard, but it should be read as employer-backed development rather than ordinary household burn.",
            "reimbursable": True,
        },
    ]

    for spec in event_specs:
        row = conn.execute(spec["match_sql"]).fetchone()
        if not row:
            continue
        month = str(row["transaction_date"])[:7]
        month_rows = conn.execute(
            """
            SELECT
                COALESCE(ta.category, 'Uncategorized') AS category,
                SUM(CASE WHEN nt.amount < 0 THEN ABS(nt.amount) ELSE 0 END) AS outflow
            FROM normalized_transactions nt
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.is_duplicate = 0
              AND substr(nt.transaction_date, 1, 7) = ?
            GROUP BY category
            ORDER BY outflow DESC
            LIMIT 3
            """,
            (month,),
        ).fetchall()
        events.append(
            {
                "date": row["transaction_date"],
                "month": month,
                "label": spec["label"],
                "merchant": row["normalized_merchant"] or spec["merchant"],
                "amount": money(abs(float(row["amount"]))),
                "category": row["category"],
                "subcategory": row["subcategory"],
                "description": row["description"],
                "why": spec["why"],
                "reimbursable": bool(spec.get("reimbursable", False)),
                "month_top_categories": [
                    {"category": category_row["category"], "outflow": money(category_row["outflow"])}
                    for category_row in month_rows
                ],
            }
        )

    other_rows = conn.execute(
        """
        SELECT
            nt.transaction_date,
            nt.amount,
            nt.normalized_merchant,
            nt.description,
            COALESCE(ta.category, 'Uncategorized') AS category,
            COALESCE(ta.subcategory, '') AS subcategory,
            ta.confidence
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE nt.is_duplicate = 0
          AND ta.category = 'Other'
        ORDER BY ABS(nt.amount) DESC, nt.transaction_date DESC
        """
    ).fetchall()
    other_breakdown = [
        {
            "date": row["transaction_date"],
            "month": str(row["transaction_date"])[:7],
            "merchant": row["normalized_merchant"] or "Unknown merchant",
            "amount": money(abs(float(row["amount"]))),
            "subcategory": row["subcategory"],
            "confidence": money(float(row["confidence"] or 0)),
            "description": row["description"],
        }
        for row in other_rows
    ]

    jump_drivers: List[Dict[str, Any]] = []
    years = [row["year"] for row in year_rows]
    for prior_year, current_year in zip(years, years[1:]):
        prior = yearly_lookup[prior_year]
        current = yearly_lookup[current_year]
        burn_delta = float(current["household_spend_cashflow"]) - float(prior["household_spend_cashflow"])
        income_delta = float(current["real_income"]) - float(prior["real_income"])
        jump_drivers.append(
            {
                "prior_year": prior_year,
                "current_year": current_year,
                "burn_delta": money(burn_delta),
                "income_delta": money(income_delta),
                "note": (
                    "Income outran burn" if income_delta > burn_delta else "Spending outpaced income"
                ),
            }
        )

    summary = [
        f"The record runs from {first_year} to {last_year}, and the long trend is stronger income growth than spending growth.",
        "The visible jumps are mostly lumpy decisions: a car purchase, a heat pump, and recurring transfers/investments that make cashflow look noisier than household burn.",
        "Day-to-day categories still matter, but the biggest year-to-year moves come from housing, transportation, education, holiday, and internal wealth movement.",
        "The low-confidence pile is concentrated in Other, so the cleanest next pass is to walk that bucket from the biggest items downward.",
    ]

    return {
        "headline": "Full-history story",
        "summary": summary,
        "events": events,
        "other_breakdown": other_breakdown,
        "jump_drivers": jump_drivers,
    }


def list_amortization_rules(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            ar.*,
            nt.transaction_date,
            nt.description,
            nt.normalized_merchant
        FROM amortization_rules ar
        LEFT JOIN normalized_transactions nt ON nt.id = ar.transaction_id
        ORDER BY
            CASE ar.review_status
                WHEN 'suggested' THEN 0
                WHEN 'approved' THEN 1
                ELSE 2
            END,
            ar.annual_amount DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def opportunity_reason(category: str, controllability: float) -> str:
    if category == "Unknown Card Spend":
        return "Material card bucket; optional card import or merchant rules would improve the picture."
    if category == "Uncategorized":
        return "Classification uncertainty is large enough to affect FIRE burn."
    if controllability >= 0.75:
        return "High-control variable spending; optimization here can move savings rate without structural changes."
    if controllability >= 0.45:
        return "Partly controllable spending; trend and merchant review are more useful than blanket cuts."
    return "Mostly fixed or low-control spending; optimize by contract review, refinancing, or long-cycle decisions."


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
    unknown_card_spend = conn.execute(
        """
        SELECT COALESCE(SUM(ABS(nt.amount)), 0) AS amount
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        WHERE ta.category = 'Unknown Card Spend'
          AND nt.amount < 0
          AND nt.is_duplicate = 0
        """
    ).fetchone()["amount"]
    reimbursement_rows = conn.execute(
        """
        SELECT
            COALESCE(SUM(reimbursements_received), 0) AS received,
            COALESCE(SUM(reimbursements_cleared), 0) AS cleared
        FROM monthly_snapshots
        """
    ).fetchone()
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
    received = float(reimbursement_rows["received"] if reimbursement_rows else 0)
    cleared = float(reimbursement_rows["cleared"] if reimbursement_rows else 0)
    return {
        "transactions": total,
        "annotated": annotated,
        "duplicates": duplicate,
        "open_review_items": review,
        "needs_review_amount": money(needs_review_amount),
        "unknown_card_spend": money(unknown_card_spend),
        "reimbursements_received": money(received),
        "reimbursements_cleared": money(cleared),
        "reimbursement_uncleared": money(received - cleared),
        "confidence_by_value": confidence,
    }
