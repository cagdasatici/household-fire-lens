import tempfile
import unittest
from pathlib import Path

from household_fire_lens.aggregation import fire_snapshot, optimization_insights, recompute_monthly_snapshots
from household_fire_lens.classifier import classify_all, create_rule_from_review
from household_fire_lens.database import connect_database
from household_fire_lens.importer import import_csv
from household_fire_lens.parsers import parse_transactions


ING_CSV = """Datum;Naam / Omschrijving;Rekening;Tegenrekening;Code;Af Bij;Bedrag (EUR);MutatieSoort;Mededelingen
2026-01-26;Booking.com Payroll;NL01INGB0000000001;NL99BOOK0000000001;GT;Bij;5000,00;Overschrijving;SALARY JAN
2026-02-25;Booking.com Payroll;NL01INGB0000000001;NL99BOOK0000000001;GT;Bij;5000,00;Overschrijving;SALARY FEB
2026-03-25;Booking.com Payroll;NL01INGB0000000001;NL99BOOK0000000001;GT;Bij;5100,00;Overschrijving;SALARY MAR
2026-01-03;Albert Heijn Amsterdam;NL01INGB0000000001;NL11SHOP0000000001;BA;Af;100,00;Betaalautomaat;Groceries
2026-01-05;VISA CREDITCARD;NL01INGB0000000001;NL22CARD0000000001;GT;Af;300,00;Overschrijving;Card settlement
2026-01-10;Booking.com Expense Pay;NL01INGB0000000001;NL99BOOK0000000001;GT;Bij;120,00;Overschrijving;Expense reimbursement
2026-01-12;IBKR;NL01INGB0000000001;NL33IBKR0000000001;GT;Af;1000,00;Overschrijving;Interactive Brokers deposit
2026-01-13;Albert Heijn Refund;NL01INGB0000000001;NL11SHOP0000000001;GT;Bij;20,00;Overschrijving;Refund groceries
"""


ABN_CSV = """Boekdatum;Omschrijving;Rekeningnummer;Tegenrekeningnummer;Naam tegenpartij;Bedrag;Valuta
2026-01-02;Hypotheek maandbetaling;NL02ABNA0000000002;NL44MORT0000000001;ABN AMRO Hypotheek;-1500,00;EUR
2026-01-20;Vattenfall Energie;NL02ABNA0000000002;NL55UTIL0000000001;Vattenfall;-180,00;EUR
"""


ABN_HEADERLESS_TAB = (
    "123456789\tEUR\t20250103\t658,78\t804,23\t20250103\t145,45\tSEPA refund text\r\n"
    "123456789\tEUR\t20250104\t804,23\t791,89\t20250104\t-12,34\tSEPA card payment text\r\n"
)


ING_AMOUNT_EUR_CSV = """Date,Name / Description,Account,Counterparty,Code,Debit/credit,Amount (EUR),Transaction type,Notifications,Resulting balance,Tag
2026-07-01,Example Salary,NL00INGB0000000000,NL00BANK0000000000,GT,Credit,"5000,00",Transfer,Salary text,"10000,00",
2026-07-02,Example Shop,NL00INGB0000000000,NL00SHOP0000000000,BA,Debit,"12,34",Card,Shop text,"9987,66",
"""


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.conn = connect_database(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def import_and_classify(self):
        import_csv(
            self.conn,
            "synthetic-main.csv",
            ING_CSV.encode("utf-8"),
            institution="ing",
            account_role="checking",
            account_hint="ING Checking",
        )
        import_csv(
            self.conn,
            "synthetic-fixed.csv",
            ABN_CSV.encode("utf-8"),
            institution="abn",
            account_role="checking",
            account_hint="ABN Fixed",
        )
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)

    def test_salary_reimbursement_investment_refund_and_mortgage_math(self):
        self.import_and_classify()
        january = self.conn.execute("SELECT * FROM monthly_snapshots WHERE month = '2026-01'").fetchone()
        self.assertIsNotNone(january)
        self.assertEqual(january["real_income"], 5000.0)
        self.assertEqual(january["wealth_allocation"], 1000.0)
        self.assertEqual(january["reimbursements_received"], 120.0)
        self.assertEqual(january["reimbursements_cleared"], 120.0)
        self.assertEqual(january["refunds"], 20.0)
        self.assertEqual(january["mortgage_total"], 1500.0)
        # Groceries 100 + card 300 + utility 180 + mortgage 1500 - refund 20 - reimbursement 120.
        self.assertEqual(january["household_spend_normalized"], 1940.0)

    def test_duplicate_file_does_not_double_count(self):
        first = import_csv(
            self.conn,
            "synthetic-main.csv",
            ING_CSV.encode("utf-8"),
            institution="ing",
            account_role="checking",
        )
        second = import_csv(
            self.conn,
            "synthetic-main-again.csv",
            ING_CSV.encode("utf-8"),
            institution="ing",
            account_role="checking",
        )
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "duplicate_file")
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        snapshot = fire_snapshot(self.conn)
        self.assertEqual(len(snapshot["months"]), 3)

    def test_review_rule_can_be_created_and_reused(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Mystery merchant,Mystery Shop,-250.00,EUR
2026-04-10,Main,Mystery merchant second,Mystery Shop,-80.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-generic.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        review_count = self.conn.execute("SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'").fetchone()["count"]
        self.assertGreaterEqual(review_count, 1)

    def test_amortization_replaces_lumpy_cashflow_when_approved(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-01-04,Main,Annual insurance premium,Allianz Insurance,-1200.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-insurance.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        recompute_monthly_snapshots(self.conn)
        rule = self.conn.execute("SELECT * FROM amortization_rules WHERE review_status = 'suggested'").fetchone()
        self.assertIsNotNone(rule)
        january = self.conn.execute("SELECT * FROM monthly_snapshots WHERE month = '2026-01'").fetchone()
        self.assertEqual(january["household_spend_normalized"], 1200.0)

        self.conn.execute("UPDATE amortization_rules SET review_status = 'approved' WHERE id = ?", (rule["id"],))
        self.conn.commit()
        recompute_monthly_snapshots(self.conn)
        january = self.conn.execute("SELECT * FROM monthly_snapshots WHERE month = '2026-01'").fetchone()
        self.assertEqual(january["household_spend_cashflow"], 1200.0)
        self.assertEqual(january["household_spend_normalized"], 100.0)

    def test_optimization_insights_surface_controllable_categories(self):
        self.import_and_classify()
        insights = optimization_insights(self.conn)
        categories = {item["category"] for item in insights["opportunities"]}
        self.assertIn("Unknown Card Spend", categories)
        self.assertGreaterEqual(insights["summary"]["months_loaded"], 3)

    def test_headerless_abn_tab_export_parses(self):
        institution, parsed = parse_transactions(
            "TXT260702214417.TAB",
            ABN_HEADERLESS_TAB.encode("utf-8"),
            institution="abn",
        )
        self.assertEqual(institution, "abn")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].transaction_date, "2025-01-03")
        self.assertEqual(parsed[0].amount, 145.45)
        self.assertEqual(parsed[1].amount, -12.34)
        self.assertEqual(parsed[0].currency, "EUR")

    def test_ing_amount_eur_export_parses(self):
        institution, parsed = parse_transactions(
            "NL00INGB0000000000_01-07-2025_01-07-2026.csv",
            ING_AMOUNT_EUR_CSV.encode("utf-8"),
            institution="ing",
        )
        self.assertEqual(institution, "ing")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].amount, 5000.0)
        self.assertEqual(parsed[1].amount, -12.34)
        self.assertIn("Salary text", parsed[0].description)

    def test_unscoped_review_rule_does_not_classify_everything(self):
        self.import_and_classify()
        self.conn.execute(
            """
            INSERT INTO classification_rules (
                name, priority, conditions_json, actions_json, confidence, created_by, enabled
            ) VALUES (
                'Bad broad rule', 1, '{"min_abs_amount": 0}',
                '{"economic_class": "wealth_allocation", "category": "Investments", "subcategory": ""}',
                0.96, 'user', 1
            )
            """
        )
        self.conn.commit()
        classify_all(self.conn)
        counts = {
            row["economic_class"]: row["count"]
            for row in self.conn.execute(
                "SELECT economic_class, COUNT(*) AS count FROM transaction_annotations GROUP BY economic_class"
            )
        }
        self.assertGreater(counts.get("household_spend", 0), 0)
        self.assertGreater(counts.get("income", 0), 0)

    def test_investment_review_rule_is_transaction_specific(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Mystery transfer,, -250.00,EUR
2026-04-02,Main,Mystery transfer,, -80.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-risky-review.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        tx_id = self.conn.execute(
            "SELECT id FROM normalized_transactions WHERE amount = -250"
        ).fetchone()["id"]
        rule_id = create_rule_from_review(self.conn, tx_id, "wealth_allocation", "Investments")
        self.conn.commit()
        classify_all(self.conn)
        rule = self.conn.execute("SELECT conditions_json FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
        self.assertIn("transaction_id", rule["conditions_json"])
        wealth_count = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM transaction_annotations
            WHERE economic_class = 'wealth_allocation'
            """
        ).fetchone()["count"]
        self.assertEqual(wealth_count, 1)


if __name__ == "__main__":
    unittest.main()
