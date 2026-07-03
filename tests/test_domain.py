import tempfile
import unittest
from pathlib import Path

from household_fire_lens.aggregation import fire_snapshot, optimization_insights, recompute_monthly_snapshots
from household_fire_lens.classifier import classify_all, create_rule_from_review
from household_fire_lens.database import connect_database
from household_fire_lens.entity_resolver import candidate_merchants_for_enrichment, is_lookup_safe, resolve_merchant, store_user_entity_mapping
from household_fire_lens.importer import import_csv
from household_fire_lens.parsers import normalize_merchant, parse_transactions


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


ING_DESCRIPTION_IBAN_CSV = """Date,Name / Description,Account,Counterparty,Code,Debit/credit,Amount (EUR),Transaction type,Notifications,Resulting balance,Tag
2026-07-02,Own Transfer,NL00INGB0000000000,,GT,Debit,"1500,00",Transfer,"Account: NL00INGB0000000000 Name: Me IBAN: NL91ABNA0417164300 Value date: 02/07/2026","8500,00",
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

    def test_split_payroll_and_bonus_are_income_without_pay_window_false_positives(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-01-24,ING Main,Wage/Salary 202601 ING,Booking.com Payroll,5200.00,EUR
2026-01-24,ABN Fixed,Wage/Salary 202601 ABN,Booking.com Payroll,2700.00,EUR
2026-01-24,ING Main,Oranje Spaarrekening from savings,Oranje Spaarrekening,5000.00,EUR
2026-01-24,ING Main,Tikkie received for party,AAB INZ TIKKIE,6.00,EUR
2026-02-25,ING Main,Wage/Salary 202602 ING,Booking.com Payroll,40750.00,EUR
2026-02-25,ABN Fixed,Wage/Salary 202602 ABN,Booking.com Payroll,2700.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-split-payroll.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = {
            row["description"]: row
            for row in self.conn.execute(
                """
                SELECT nt.description, ta.economic_class, ta.category, ta.subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        self.assertEqual(rows["Wage/Salary 202601 ING"]["economic_class"], "income")
        self.assertEqual(rows["Wage/Salary 202601 ING"]["subcategory"], "Salary")
        self.assertEqual(rows["Wage/Salary 202601 ABN"]["economic_class"], "income")
        self.assertEqual(rows["Wage/Salary 202601 ABN"]["subcategory"], "Salary")
        self.assertEqual(rows["Oranje Spaarrekening from savings"]["economic_class"], "internal_transfer")
        self.assertEqual(rows["Tikkie received for party"]["economic_class"], "reimbursement_pass_through")
        self.assertEqual(rows["Wage/Salary 202602 ING"]["subcategory"], "Cash Bonus")
        self.assertEqual(rows["Wage/Salary 202602 ABN"]["subcategory"], "Salary")

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

    def test_holiday_and_other_buckets_are_available(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-05-01,Main,Hotel booking,Booking.com,-450.00,EUR
2026-05-02,Main,Tiny unknown merchant,Mystery,-12.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-holiday-other.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        categories = {
            row["category"]
            for row in self.conn.execute(
                "SELECT category FROM transaction_annotations"
            ).fetchall()
        }
        self.assertIn("Holiday", categories)
        self.assertIn("Other", categories)

    def test_cash_withdrawal_terminal_and_processor_descriptions_are_not_review(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-05-01,Main,Geldmaat cash withdrawal,Geldmaat,-200.00,EUR
2026-05-02,Main,ZETTLE BROWN LASER CL,ZETTLE BROWN LASER CL,-85.00,EUR
2026-05-03,Main,ALBERT HEIJN AMSTELVEEN NLD PAYMENT TERMINAL CARD NO 18 DATE 02 05 TIME 17 13 TRANSACTION I14134 TER,ALBERT HEIJN AMSTELVEEN NLD PAYMENT TERMINAL CARD NO 18 DATE 02 05 TIME 17 13 TRANSACTION I14134 TER,-34.56,EUR
"""
        import_csv(
            self.conn,
            "synthetic-terminal-cleanup.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = {
            row["normalized_merchant"]: row
            for row in self.conn.execute(
                """
                SELECT nt.normalized_merchant, ta.economic_class, ta.category, ta.subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(rows["GELDMAAT"]["category"], "Cash Withdrawal")
        self.assertEqual(rows["BROWN LASER CL"]["category"], "Other")
        self.assertEqual(rows["BROWN LASER CL"]["subcategory"], "Payment Processor")
        self.assertEqual(rows["ALBERT HEIJN AMSTELVEEN"]["category"], "Groceries")
        self.assertEqual(review_count, 0)

    def test_savings_keyword_uses_inter_account_transfer_bucket(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-05-01,Main,Savings transfer to own account,Own savings,-250.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-transfer-bucket.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        row = self.conn.execute(
            "SELECT economic_class, category, subcategory FROM transaction_annotations"
        ).fetchone()
        self.assertEqual(row["economic_class"], "internal_transfer")
        self.assertEqual(row["category"], "Inter-account Transfers")
        self.assertEqual(row["subcategory"], "Savings")

    def test_sepa_names_payment_requests_and_bank_transfers_are_not_review(self):
        self.assertEqual(
            normalize_merchant("SEPA OVERBOEKING IBAN BIC RABONL2U NAAM SOCIALE VERZEKERINGSBANK OMSCHRIJVING KINDER"),
            "SOCIALE VERZEKERINGSBANK",
        )
        self.assertEqual(
            normalize_merchant("KLM N.V. Transfer Name: KLM N.V. Description: Refund IBAN: NL19INGB0000787900"),
            "KLM N.V.",
        )
        self.assertEqual(
            normalize_merchant("/TRTP/SEPA OVERBOEKING/IBAN/NL86INGB0002445588/BIC/INGBNL2A/NAME/BELASTINGDIENST/REMI/TERUGGAAF"),
            "BELASTINGDIENST",
        )
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-06-01,Main,Tikkie betaald aan friend,, -42.50,EUR
2026-06-02,Main,Tikkie ontvangen van friend,,42.50,EUR
2026-06-03,Main,SEPA OVERBOEKING IBAN BIC RABONL2U NAAM RANDOM PERSON OMSCHRIJVING dinner,, -85.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-payment-request-transfer.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT nt.description, ta.economic_class, ta.category, ta.subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                ORDER BY nt.transaction_date
                """
            ).fetchall()
        ]
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(rows[0]["economic_class"], "household_spend")
        self.assertEqual(rows[0]["category"], "Other")
        self.assertEqual(rows[0]["subcategory"], "Payment Request")
        self.assertEqual(rows[1]["economic_class"], "reimbursement_pass_through")
        self.assertEqual(rows[1]["category"], "Reimbursements")
        self.assertEqual(rows[1]["subcategory"], "Payment Request")
        self.assertEqual(rows[2]["economic_class"], "household_spend")
        self.assertEqual(rows[2]["category"], "Other")
        self.assertEqual(rows[2]["subcategory"], "Bank Transfer")
        self.assertEqual(review_count, 0)

    def test_importer_falls_back_to_description_when_counterparty_is_only_iban(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,BCML Enterprise Online Banking Name: BCML Enterprise Description: Music Lessons IBAN: NL35RABO0368686434,NL35RABO0368686434,-300.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-iban-counterparty.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        row = self.conn.execute("SELECT normalized_merchant FROM normalized_transactions").fetchone()
        self.assertEqual(row["normalized_merchant"], "BCML ENTERPRISE")

    def test_uncategorized_outflows_review_only_when_material(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-06-01,Main,Small mystery merchant,Small Mystery,-85.00,EUR
2026-06-02,Main,Large mystery merchant,Large Mystery,-275.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-unknown-threshold.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = {
            row["normalized_merchant"]: row
            for row in self.conn.execute(
                """
                SELECT nt.normalized_merchant, ta.economic_class, ta.category, ta.confidence
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(rows["SMALL MYSTERY"]["economic_class"], "household_spend")
        self.assertEqual(rows["SMALL MYSTERY"]["category"], "Other")
        self.assertGreaterEqual(rows["SMALL MYSTERY"]["confidence"], 0.55)
        self.assertEqual(rows["LARGE MYSTERY"]["economic_class"], "needs_review")
        self.assertEqual(review_count, 1)

    def test_refunds_reimbursements_and_common_large_merchants_avoid_review(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-06-01,Main,ALBERT HEIJN AMSTELVEEN Cashback transaction,Albert Heijn,12.50,EUR
2026-06-02,Main,BELASTINGDIENST TERUGGAAF IB/PVV,Belastingdienst,889.00,EUR
2026-06-03,Main,Stichting example Vergoeding april,Stichting Example,100.00,EUR
2026-06-04,Main,Dierenartspraktijk West payment terminal,Dierenartspraktijk West,-1415.45,EUR
2026-06-05,Main,KWIKFIT CENTER AMSTELVEEN payment terminal,Kwikfit,-492.50,EUR
2026-06-06,Main,BCK Henders en Hazel Cruquius payment terminal,Henders en Hazel,-316.00,EUR
2026-06-07,Main,Riverty GmbH iDEAL,Riverty GmbH,-1150.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-common-household-patterns.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = {
            row["normalized_merchant"]: row
            for row in self.conn.execute(
                """
                SELECT nt.normalized_merchant, ta.economic_class, ta.category, ta.subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(rows["ALBERT HEIJN"]["economic_class"], "refund")
        self.assertEqual(rows["ALBERT HEIJN"]["category"], "Groceries")
        self.assertEqual(rows["BELASTINGDIENST"]["economic_class"], "refund")
        self.assertEqual(rows["BELASTINGDIENST"]["category"], "Taxes and Government")
        self.assertEqual(rows["STICHTING EXAMPLE"]["economic_class"], "reimbursement_pass_through")
        self.assertEqual(rows["DIERENARTSPRAKTIJK WEST"]["category"], "Health")
        self.assertEqual(rows["KWIKFIT"]["category"], "Transportation")
        self.assertEqual(rows["HENDERS EN HAZEL"]["category"], "Home and Furniture")
        self.assertEqual(rows["RIVERTY GMBH"]["category"], "Other")
        self.assertEqual(rows["RIVERTY GMBH"]["subcategory"], "Payment Processor")
        self.assertEqual(review_count, 0)

    def test_remediation_golden_fixture_classifies_metric_critical_rows(self):
        fixture = Path(__file__).parent / "fixtures" / "remediation-golden.csv"
        import_csv(
            self.conn,
            "remediation-golden.csv",
            fixture.read_bytes(),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        loan_rows_before = self.conn.execute(
            """
            SELECT nt.id, ta.economic_class, ta.category, ta.subcategory
            FROM normalized_transactions nt
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.description LIKE '%Example Loan Bank%'
            ORDER BY nt.transaction_date
            """
        ).fetchall()
        self.assertEqual(len(loan_rows_before), 6)
        self.assertEqual({row["economic_class"] for row in loan_rows_before}, {"needs_review"})
        review = self.conn.execute(
            """
            SELECT suggested_action_json, materiality
            FROM review_items
            WHERE transaction_id = ?
            """,
            (loan_rows_before[0]["id"],),
        ).fetchone()
        self.assertIsNotNone(review)
        self.assertIn("recurring_direct_debit", review["suggested_action_json"])

        rule_id = create_rule_from_review(
            self.conn,
            loan_rows_before[0]["id"],
            "debt_service",
            "Housing",
            "Mortgage",
        )
        classify_all(self.conn)
        rows = {
            row["description"]: row
            for row in self.conn.execute(
                """
                SELECT nt.description, ta.economic_class, ta.category, ta.subcategory, ta.rule_id
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        loan_rows_after = [
            row
            for row in self.conn.execute(
                """
                SELECT ta.economic_class, ta.category, ta.subcategory, ta.rule_id
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                WHERE nt.description LIKE '%Example Loan Bank%'
                """
            ).fetchall()
        ]

        self.assertEqual(rows["iDEAL Name Flatex Bank AG Description CASHORDER756656 Cash Order"]["economic_class"], "wealth_allocation")
        self.assertEqual(rows["iDEAL Name Flatex Bank AG Description CASHORDER756656 Cash Order"]["category"], "Investments")
        self.assertEqual({row["economic_class"] for row in loan_rows_after}, {"debt_service"})
        self.assertEqual({row["subcategory"] for row in loan_rows_after}, {"Mortgage"})
        self.assertEqual({row["rule_id"] for row in loan_rows_after}, {rule_id})
        self.assertNotEqual(rows["Comfort Partners B.V. Online Banking Name Comfort Partners B.V. Description home installation invoice"]["category"], "Banking and Fees")
        self.assertEqual(rows["Comfort Partners B.V. Online Banking Name Comfort Partners B.V. Description home installation invoice"]["economic_class"], "needs_review")
        self.assertEqual(rows["American Express Europe S.A. Incasso creditcard"]["category"], "Unknown Card Spend")
        self.assertEqual(rows["ING Incasso Creditcard ICS monthly settlement"]["category"], "Unknown Card Spend")
        self.assertEqual(rows["Booking.com B.V. SALARY JAN"]["economic_class"], "income")
        self.assertEqual(rows["Booking.com B.V. Expense reimbursement"]["economic_class"], "reimbursement_pass_through")
        self.assertEqual(rows["Tikkie betaald aan Friend"]["subcategory"], "Payment Request")
        self.assertEqual(rows["Tikkie ontvangen van Friend"]["economic_class"], "reimbursement_pass_through")
        self.assertEqual(rows["VOMAR VOORDEELMARKT AMSTELVEEN payment terminal"]["category"], "Groceries")
        self.assertEqual(rows["KRUIDVAT AMSTELVEEN payment terminal"]["category"], "Health")
        self.assertEqual(rows["ACTION AALSMEER payment terminal"]["category"], "Shopping")
        self.assertEqual(rows["BOL.COM refund"]["economic_class"], "refund")
        self.assertEqual(rows["BOL.COM refund"]["category"], "Shopping")
        self.assertEqual(rows["Transfer to own savings"]["economic_class"], "internal_transfer")
        self.assertEqual(rows["Transfer from own checking"]["economic_class"], "internal_transfer")
        self.assertEqual(rows["CREDITRENTE savings interest"]["economic_class"], "income")
        self.assertEqual(rows["CREDITRENTE savings interest"]["category"], "Interest")
        self.assertEqual(rows["Geldmaat cash withdrawal"]["category"], "Cash Withdrawal")
        self.assertEqual(rows["SEPA OVERBOEKING NAAM SOCIALE VERZEKERINGSBANK OMSCHRIJVING KINDER"]["economic_class"], "income")
        self.assertEqual(rows["SEPA OVERBOEKING NAAM SOCIALE VERZEKERINGSBANK OMSCHRIJVING KINDER"]["subcategory"], "Child Benefit")
        self.assertEqual(rows["NS International train ticket"]["category"], "Transportation")
        self.assertEqual(rows["Example Hotel booking"]["category"], "Holiday")
        self.assertEqual(rows["Gemeente local tax"]["category"], "Taxes and Government")

    def test_mollie_payment_processor_keeps_embedded_merchant(self):
        self.assertEqual(
            normalize_merchant("Van Dulken via Stichting Mollie Payments"),
            "VAN DULKEN",
        )

    def test_unknown_mollie_processor_payment_defaults_to_other(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-02,Main,Van Dulken via Stichting Mollie Payments,Van Dulken via Stichting Mollie Payments,-65.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-mollie-processor.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        row = self.conn.execute(
            "SELECT economic_class, category, subcategory FROM transaction_annotations"
        ).fetchone()
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(row["economic_class"], "household_spend")
        self.assertEqual(row["category"], "Other")
        self.assertEqual(row["subcategory"], "Payment Processor")
        self.assertEqual(review_count, 0)

    def test_free_public_entity_cache_classifies_merchant(self):
        def fake_fetch(_url):
            return {
                "search": [
                    {
                        "id": "Q123",
                        "label": "Van Dulken",
                        "description": "dental clinic in the Netherlands",
                        "aliases": [],
                    }
                ]
            }

        result = resolve_merchant(self.conn, "Van Dulken", fetch_json=fake_fetch)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["category"], "Health")

        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-02,Main,Van Dulken via Stichting Mollie Payments,Van Dulken via Stichting Mollie Payments,-65.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-public-entity-cache.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        row = self.conn.execute(
            "SELECT economic_class, category, explanation FROM transaction_annotations"
        ).fetchone()
        self.assertEqual(row["economic_class"], "household_spend")
        self.assertEqual(row["category"], "Health")
        self.assertIn("Free public entity lookup", row["explanation"])

    def test_entity_enrichment_candidates_include_other_and_low_confidence_merchants(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Small local merchant,Small Local Merchant,-85.00,EUR
2026-04-02,Main,Large local merchant,Large Local Merchant,-275.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-enrichment-candidates.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        candidates = candidate_merchants_for_enrichment(self.conn, limit=10)
        self.assertIn("SMALL LOCAL MERCHANT", candidates)
        self.assertIn("LARGE LOCAL MERCHANT", candidates)

    def test_user_review_mapping_persists_as_entity_hint(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Local shop visit,Local Shop,-85.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-user-entity-map.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        self.assertTrue(
            store_user_entity_mapping(
                self.conn,
                "LOCAL SHOP",
                "household_spend",
                "Shopping",
                "",
            )
        )
        classify_all(self.conn)
        row = self.conn.execute(
            """
            SELECT ta.economic_class, ta.category, ta.explanation
            FROM transaction_annotations ta
            """
        ).fetchone()
        cache = self.conn.execute(
            "SELECT source, status, confidence FROM entity_enrichment_cache WHERE lookup_key = 'LOCAL SHOP'"
        ).fetchone()
        self.assertEqual(row["economic_class"], "household_spend")
        self.assertEqual(row["category"], "Shopping")
        self.assertIn("Free public entity lookup", row["explanation"])
        self.assertEqual(cache["source"], "user")
        self.assertEqual(cache["status"], "resolved")
        self.assertEqual(cache["confidence"], 1.0)

    def test_grouped_review_rule_does_not_resurrect_after_reclassify(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Recurring mystery debit,Mystery Recurring,-300.00,EUR
2026-04-02,Main,Recurring mystery debit,Mystery Recurring,-80.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-grouped-review.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        tx_id = self.conn.execute(
            "SELECT id FROM normalized_transactions WHERE amount = -300"
        ).fetchone()["id"]
        rule_id = create_rule_from_review(self.conn, tx_id, "household_spend", "Shopping")
        self.conn.commit()
        classify_all(self.conn)
        review_count_after = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        rows = self.conn.execute(
            "SELECT economic_class, category, rule_id FROM transaction_annotations"
        ).fetchall()
        self.assertEqual(review_count, 1)
        self.assertEqual(review_count_after, 0)
        self.assertEqual({row["category"] for row in rows}, {"Shopping"})
        self.assertEqual({row["rule_id"] for row in rows}, {rule_id})

    def test_bank_transfer_review_rule_scopes_to_counterparty_group(self):
        csv_text = """Date,Account,Description,Counterparty,Counterparty account,Amount,Currency
2026-04-01,Main,Online Banking Name: Music School Description: April lessons,NL35RABO0368686434,NL35RABO0368686434,-300.00,EUR
2026-04-02,Main,Online Banking Name: Music School Description: May lessons,NL35RABO0368686434,NL35RABO0368686434,-280.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-counterparty-rule.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        self.conn.execute("UPDATE normalized_transactions SET normalized_merchant = ''")
        self.conn.commit()
        classify_all(self.conn)
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        tx_id = self.conn.execute(
            "SELECT id FROM normalized_transactions WHERE amount = -300"
        ).fetchone()["id"]
        rule_id = create_rule_from_review(self.conn, tx_id, "household_spend", "Education")
        self.conn.commit()
        classify_all(self.conn)
        review_count_after = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        rows = self.conn.execute(
            "SELECT economic_class, category, rule_id FROM transaction_annotations"
        ).fetchall()
        rule = self.conn.execute(
            "SELECT conditions_json FROM classification_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        self.assertEqual(review_count, 1)
        self.assertEqual(review_count_after, 0)
        self.assertEqual({row["category"] for row in rows}, {"Education"})
        self.assertEqual({row["rule_id"] for row in rows}, {rule_id})
        self.assertIn("counterparty_account_hash", rule["conditions_json"])

    def test_openstreetmap_entity_cache_is_preferred_for_local_places(self):
        def fake_fetch(url):
            if "nominatim.openstreetmap.org" in url:
                return [
                    {
                        "osm_type": "node",
                        "osm_id": 123,
                        "name": "Van Dulken",
                        "display_name": "Van Dulken, Amsterdam, Nederland",
                        "category": "healthcare",
                        "type": "dentist",
                        "extratags": {"healthcare": "dentist"},
                    }
                ]
            return {"search": []}

        result = resolve_merchant(self.conn, "Van Dulken", fetch_json=fake_fetch)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["category"], "Health")
        self.assertEqual(result["source"], "openstreetmap_nominatim")

    def test_online_lookup_rejects_card_terminal_descriptions(self):
        self.assertFalse(
            is_lookup_safe("BEA APPLE PAY PAY.NL SPARNAAIJ JUWEL PAS441 NR 08TVT7 20.12.25 15 38 AALSMEER")
        )
        self.assertTrue(is_lookup_safe("VAN DULKEN"))

    def test_svb_child_benefit_is_income_not_review(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-02,Main,SEPA OVERBOEKING IBAN BIC RABONL2U NAAM SOCIALE VERZEKERINGSBANK OMSCHRIJVING KINDER,Sociale Verzekeringsbank,286.45,EUR
"""
        import_csv(
            self.conn,
            "synthetic-svb-benefit.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        row = self.conn.execute(
            "SELECT economic_class, category, subcategory FROM transaction_annotations"
        ).fetchone()
        self.assertEqual(row["economic_class"], "income")
        self.assertEqual(row["category"], "Benefits")
        self.assertEqual(row["subcategory"], "Child Benefit")

    def test_large_svb_child_benefit_does_not_become_subsidy_offset(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Main,Home improvement invoice,Home Improvement Installer,-3000.00,EUR
2026-04-02,Main,SEPA OVERBOEKING IBAN BIC RABONL2U NAAM SOCIALE VERZEKERINGSBANK OMSCHRIJVING KINDER,Sociale Verzekeringsbank,710.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-large-svb-benefit.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        row = self.conn.execute(
            """
            SELECT ta.economic_class, ta.category, ta.subcategory
            FROM normalized_transactions nt
            JOIN transaction_annotations ta ON ta.transaction_id = nt.id
            WHERE nt.amount > 0
            """
        ).fetchone()
        link_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM transaction_links WHERE link_type = 'subsidy_offset'"
        ).fetchone()["count"]
        self.assertEqual(row["economic_class"], "income")
        self.assertEqual(row["category"], "Benefits")
        self.assertEqual(row["subcategory"], "Child Benefit")
        self.assertEqual(link_count, 0)

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

    def test_ing_description_iban_becomes_counterparty_account(self):
        institution, parsed = parse_transactions(
            "NL00INGB0000000000_01-07-2025_01-07-2026.csv",
            ING_DESCRIPTION_IBAN_CSV.encode("utf-8"),
            institution="ing",
        )
        self.assertEqual(institution, "ing")
        self.assertEqual(parsed[0].counterparty_account, "NL91ABNA0417164300")

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

    def test_investment_review_rule_reuses_counterparty_account_hash(self):
        csv_text = """Date,Account,Description,Counterparty,Counter Account,Amount,Currency
2026-04-01,Main,Transfer to investment,,NL91ABNA0417164300,-250.00,EUR
2026-04-02,Main,Another transfer,,NL91ABNA0417164300,-80.00,EUR
2026-04-03,Main,Transfer back from investment,,NL91ABNA0417164300,80.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-counterparty-investment.csv",
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
        self.assertIn("counterparty_account_hash", rule["conditions_json"])
        self.assertIn("direction", rule["conditions_json"])
        wealth_count = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM transaction_annotations
            WHERE economic_class = 'wealth_allocation'
            """
        ).fetchone()["count"]
        self.assertEqual(wealth_count, 2)

    def test_rsu_income_rule_can_override_inbound_investment_counterparty_by_month(self):
        self.conn.execute(
            """
            INSERT INTO classification_rules (
                name, priority, conditions_json, actions_json, confidence, created_by, enabled
            ) VALUES (
                'Old broad investment rule', 50,
                '{"counterparty_account_hash": "stock-plan"}',
                '{"economic_class": "wealth_allocation", "category": "Investments", "subcategory": ""}',
                0.96, 'user', 1
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO classification_rules (
                name, priority, conditions_json, actions_json, confidence, created_by, enabled
            ) VALUES (
                'RSU vest proceeds', 10,
                '{"counterparty_account_hash": "stock-plan", "direction": "inflow", "months": [3, 4], "min_abs_amount": 1000}',
                '{"economic_class": "income", "category": "Equity Compensation", "subcategory": "RSU"}',
                0.98, 'user', 1
            )
            """
        )
        csv_text = """Date,Account,Description,Counterparty,Counter Account,Amount,Currency
2026-04-15,Main,Stock plan vest proceeds,Equity Plan,stock-plan,40950.00,EUR
2026-06-15,Main,Investment withdrawal,Equity Plan,stock-plan,5000.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-rsu-income.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        self.conn.execute(
            """
            UPDATE normalized_transactions
            SET counterparty_account_hash = 'stock-plan'
            """
        )
        self.conn.commit()
        classify_all(self.conn)
        rows = {
            row["description"]: row
            for row in self.conn.execute(
                """
                SELECT nt.description, ta.economic_class, ta.category, ta.subcategory
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        self.assertEqual(rows["Stock plan vest proceeds"]["economic_class"], "income")
        self.assertEqual(rows["Stock plan vest proceeds"]["category"], "Equity Compensation")
        self.assertEqual(rows["Stock plan vest proceeds"]["subcategory"], "RSU")
        self.assertEqual(rows["Investment withdrawal"]["economic_class"], "wealth_allocation")

    def test_investment_review_promotes_unknown_dedicated_account(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-04-01,Broker Cash,Trade settlement,,250.00,EUR
2026-04-02,Broker Cash,Trade fee,,-2.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-broker-cash.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="unknown",
        )
        classify_all(self.conn)
        tx_id = self.conn.execute(
            "SELECT id FROM normalized_transactions WHERE amount = 250"
        ).fetchone()["id"]
        rule_id = create_rule_from_review(self.conn, tx_id, "wealth_allocation", "Investments")
        self.conn.commit()
        classify_all(self.conn)
        rule = self.conn.execute("SELECT conditions_json FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
        account = self.conn.execute("SELECT role FROM accounts").fetchone()
        self.assertIn("account_id", rule["conditions_json"])
        self.assertEqual(account["role"], "investment")
        wealth_count = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM transaction_annotations
            WHERE economic_class = 'wealth_allocation'
            """
        ).fetchone()["count"]
        self.assertEqual(wealth_count, 2)

    def test_schema_v3_has_owner_digest_and_income_calendar_foundations(self):
        account_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(accounts)").fetchall()
        }
        annotation_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(transaction_annotations)").fetchall()
        }
        tables = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        self.assertIn("owner", account_columns)
        self.assertIn("digest_tier", annotation_columns)
        self.assertIn("known_counterparties", tables)
        self.assertIn("expected_income_events", tables)

    def test_subsidy_inflow_links_to_related_large_outflow_as_refund(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2025-12-15,Main,Home improvement heat pump invoice,Home Improvement Installer,-14972.20,EUR
2026-01-29,Main,RVO ISDE subsidie heat pump,RVO.,4200.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-subsidy-link.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        rows = {
            row["normalized_merchant"]: row
            for row in self.conn.execute(
                """
                SELECT nt.normalized_merchant, ta.economic_class, ta.category, ta.subcategory, ta.digest_tier
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        link = self.conn.execute(
            "SELECT link_type, amount, confidence FROM transaction_links WHERE link_type = 'subsidy_offset'"
        ).fetchone()
        review_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'open'"
        ).fetchone()["count"]
        self.assertEqual(rows["RVO."]["economic_class"], "refund")
        self.assertEqual(rows["RVO."]["category"], "Home and Furniture")
        self.assertEqual(rows["RVO."]["subcategory"], "")
        self.assertEqual(rows["RVO."]["digest_tier"], "auto_silent")
        self.assertIsNotNone(link)
        self.assertEqual(link["amount"], 4200.0)
        self.assertGreaterEqual(link["confidence"], 0.9)
        self.assertEqual(review_count, 0)

    def test_unlinked_large_one_off_income_becomes_review_tier(self):
        csv_text = """Date,Account,Description,Counterparty,Amount,Currency
2026-01-29,Main,One-off foundation grant,Example Foundation,4200.00,EUR
"""
        import_csv(
            self.conn,
            "synthetic-one-off-income.csv",
            csv_text.encode("utf-8"),
            institution="generic",
            account_role="checking",
        )
        classify_all(self.conn)
        annotation = self.conn.execute(
            "SELECT economic_class, digest_tier, confidence FROM transaction_annotations"
        ).fetchone()
        review = self.conn.execute(
            "SELECT issue_type, reason FROM review_items WHERE status = 'open'"
        ).fetchone()
        self.assertEqual(annotation["economic_class"], "needs_review")
        self.assertEqual(annotation["digest_tier"], "review")
        self.assertLess(annotation["confidence"], 0.7)
        self.assertIsNotNone(review)

    def test_missing_expected_income_event_raises_review_item(self):
        self.conn.execute(
            """
            INSERT INTO expected_income_events (
                month, event_type, expected_date, expected_amount, tolerance_amount, status
            ) VALUES ('2026-05', 'salary_ing', '2026-05-25', 5200.00, 250.00, 'expected')
            """
        )
        self.conn.commit()
        classify_all(self.conn)
        review = self.conn.execute(
            """
            SELECT ri.issue_type, ri.materiality, eie.event_type, eie.month
            FROM review_items ri
            JOIN expected_income_events eie ON eie.id = ri.expected_event_id
            WHERE ri.status = 'open'
            """
        ).fetchone()
        self.assertIsNotNone(review)
        self.assertEqual(review["issue_type"], "missing_income_event")
        self.assertEqual(review["event_type"], "salary_ing")
        self.assertEqual(review["month"], "2026-05")
        self.assertEqual(review["materiality"], 5200.0)

    def test_digest_tiers_are_persisted_for_auto_classifications(self):
        self.import_and_classify()
        rows = {
            row["subcategory"] or row["category"]: row["digest_tier"]
            for row in self.conn.execute(
                """
                SELECT ta.category, ta.subcategory, ta.digest_tier
                FROM normalized_transactions nt
                JOIN transaction_annotations ta ON ta.transaction_id = nt.id
                """
            ).fetchall()
        }
        self.assertEqual(rows["Salary"], "auto_silent")
        self.assertEqual(rows["Unknown Card Spend"], "auto_visible")


if __name__ == "__main__":
    unittest.main()
