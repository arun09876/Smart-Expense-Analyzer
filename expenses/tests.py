import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import categorize
from .analysis import detect_recurring
from .models import Statement, Transaction
from .parsers import StatementParseError, extract_merchant, parse_csv, parse_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "sample_statement.csv"


class ParserTests(SimpleTestCase):
    def test_hdfc_style_with_preamble(self):
        df = parse_csv(SAMPLE.read_bytes())
        self.assertGreater(len(df), 100)
        self.assertEqual(set(df["direction"]), {"debit", "credit"})
        self.assertTrue((df["amount"] > 0).all())

    def test_signed_amount_column(self):
        csv = b"Transaction Date,Description,Amount\n2026-07-01,UPI/SWIGGY/x@ybl,-250.50\n2026-07-02,SALARY,50000\n"
        df = parse_csv(csv)
        self.assertEqual(df["direction"].tolist(), ["debit", "credit"])
        self.assertEqual(df["amount"].tolist(), [250.5, 50000.0])

    def test_amount_with_type_column_and_rupee_symbols(self):
        csv = "Date,Particulars,Amount,Dr/Cr\n05/07/2026,ATM WDL,\"₹2,000.00\",DR\n06/07/2026,Opening Balance,100,CR\n".encode()
        df = parse_csv(csv)
        self.assertEqual(len(df), 1)  # opening balance row dropped
        self.assertEqual(df.iloc[0]["amount"], 2000.0)

    def test_missing_columns_error(self):
        with self.assertRaises(StatementParseError):
            parse_csv(b"foo,bar\n1,2\n")

    def test_merchant_extraction(self):
        self.assertEqual(extract_merchant("UPI/DR/412345678901/SWIGGY/YESB/swiggy@yesbank/Payment"), "Swiggy")
        self.assertEqual(extract_merchant("NEFT DR-HDFC0001234-SURESH REDDY RENT-NETBANK"), "Suresh Reddy Rent")
        self.assertEqual(extract_merchant("ACH D- ZERODHA BROKING SIP-MF"), "Zerodha Broking Sip")

    def test_pdf_text_fallback(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        lines = ["01/07/2026 UPI-SWIGGY-swiggy@yesbank 450.00 Dr 10000.00 Cr",
                 "02/07/2026 SALARY ACME 50000.00 Cr 60000.00 Cr"]
        fig = plt.figure(figsize=(8.5, 3))
        for i, line in enumerate(lines):
            fig.text(0.05, 0.8 - i * 0.2, line, family="monospace", fontsize=9)
        buf = io.BytesIO()
        fig.savefig(buf, format="pdf")
        plt.close(fig)
        df = parse_pdf(buf.getvalue())
        self.assertEqual(df["direction"].tolist(), ["debit", "credit"])
        self.assertEqual(df["amount"].tolist(), [450.0, 50000.0])


class CategoryTests(SimpleTestCase):
    def test_rules_use_word_boundaries(self):
        self.assertEqual(categorize.rule_category("UPI-SWIGGY", "Swiggy", "debit"), "Food & Dining")
        self.assertIsNone(categorize.rule_category("PUBLIC CURRENT ACCOUNT FEE", "Public", "debit"))
        self.assertEqual(categorize.rule_category("NEFT HOUSE RENT", "House Rent", "debit"), "Rent & Housing")

    def test_credits_default_to_transfer_or_income(self):
        self.assertEqual(categorize.rule_category("UPI-MOM-x@ybl", "Mom", "credit"), "Transfers")
        self.assertEqual(categorize.rule_category("INTEREST PAID", "Interest", "credit"), "Income")

    def test_recurring_detection(self):
        dates = pd.to_datetime(["2026-06-05", "2026-07-05", "2026-08-05", "2026-06-09", "2026-06-20", "2026-07-30"])
        df = pd.DataFrame({
            "date": dates, "merchant": ["Netflix"] * 3 + ["Swiggy"] * 3,
            "amount": [649, 649, 649, 250, 410, 199], "direction": ["debit"] * 6,
        })
        rec = detect_recurring(df)
        self.assertEqual(rec["merchant"].tolist(), ["Netflix"])
        self.assertEqual(rec.iloc[0]["frequency"], "Monthly")


def fake_llm_response(payload):
    items = json.loads(payload)
    text = json.dumps({"results": [{"id": it["id"], "category": "Food & Dining"} for it in items]})
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class LLMCategoryTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch.object(categorize.anthropic, "Anthropic")
    def test_batches_and_caches_per_merchant(self, client_cls):
        create = client_cls.return_value.beta.messages.create
        create.side_effect = lambda **kw: fake_llm_response(kw["messages"][0]["content"])
        items = [{"merchant": "Sri Lakshmi Tiffins", "description": "UPI-SRI LAKSHMI TIFFINS"}]
        self.assertEqual(categorize.llm_categories(items), {"Sri Lakshmi Tiffins": "Food & Dining"})
        self.assertEqual(categorize.llm_categories(items), {"Sri Lakshmi Tiffins": "Food & Dining"})
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")

    @mock.patch.object(categorize.anthropic, "Anthropic")
    def test_llm_errors_are_non_fatal(self, client_cls):
        client_cls.return_value.beta.messages.create.side_effect = TypeError("no credentials")
        self.assertEqual(categorize.llm_categories([{"merchant": "X Traders", "description": "UPI-X"}]), {})


class FlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("meena", password="pass12345!")
        self.client.login(username="meena", password="pass12345!")

    @mock.patch("expenses.tasks.llm_categories", return_value={"Sri Lakshmi Tiffins": "Food & Dining"})
    def test_upload_process_and_dashboard(self, _):
        upload = SimpleUploadedFile("statement.csv", SAMPLE.read_bytes(), content_type="text/csv")
        resp = self.client.post(reverse("upload"), {"file": upload})
        statement = Statement.objects.get()
        self.assertRedirects(resp, reverse("statement_status", args=[statement.pk]))

        status = self.client.get(reverse("statement_status", args=[statement.pk]), HTTP_ACCEPT="application/json").json()
        self.assertEqual(status["status"], "done")
        self.assertGreater(status["rows"], 100)

        txns = Transaction.objects.filter(statement=statement)
        self.assertTrue(txns.filter(merchant="Netflix", is_recurring=True).exists())
        self.assertEqual(txns.get(merchant="Sri Lakshmi Tiffins", date__day=7, date__month=6).category_source, "llm")
        self.assertFalse(txns.filter(category_source="none").exclude(category="Other").exists())

        dash = self.client.get(reverse("dashboard"))
        self.assertContains(dash, "Recurring payments")
        self.assertContains(dash, "Netflix")
        for name in ("monthly", "categories", "merchants"):
            png = self.client.get(reverse("chart", args=[name]))
            self.assertEqual(png["Content-Type"], "image/png")

    def test_rejects_non_statement_files(self):
        bad = SimpleUploadedFile("photo.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        self.client.post(reverse("upload"), {"file": bad})
        fake_pdf = SimpleUploadedFile("s.pdf", b"hello", content_type="application/pdf")
        self.client.post(reverse("upload"), {"file": fake_pdf})
        self.assertFalse(Statement.objects.exists())

    def test_bad_csv_marks_statement_failed(self):
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("x.csv", b"a,b\n1,2\n")})
        st = Statement.objects.get()
        self.assertEqual(st.status, "failed")
        self.assertIn("date/description", st.error)

    @override_settings(LLM_ENABLED=False)
    def test_recategorise_all_from_merchant(self):
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("s.csv", SAMPLE.read_bytes())})
        txn = Transaction.objects.filter(merchant="Venkateswara Traders").first()
        resp = self.client.post(reverse("set_category", args=[txn.pk]), {"category": "Groceries", "apply_all": "1"})
        self.assertGreater(resp.json()["updated"], 1)
        self.assertFalse(Transaction.objects.filter(merchant="Venkateswara Traders").exclude(category="Groceries").exists())

    def test_other_users_cannot_see_statement(self):
        other = User.objects.create_user("x", password="pass12345!")
        st = Statement.objects.create(user=other, original_name="a.csv", file_type="csv")
        self.assertEqual(self.client.get(reverse("statement_status", args=[st.pk])).status_code, 404)
