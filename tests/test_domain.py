import tempfile
import unittest
from pathlib import Path

from household_fire_lens.aggregation import fire_snapshot, recompute_monthly_snapshots
from household_fire_lens.classifier import classify_all
from household_fire_lens.database import connect_database
from household_fire_lens.importer import import_csv


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


if __name__ == "__main__":
    unittest.main()
