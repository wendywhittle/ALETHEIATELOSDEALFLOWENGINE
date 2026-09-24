"""End-to-end web tests through the Flask test client: landing, intake
validation, submission, results, pipeline, detail, status review,
Excel download."""

import os
import re
import tempfile
import unittest

import records
from app import app


class WebCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DEALFLOW_DB"] = os.path.join(self.tmp.name, "test.db")
        os.environ["DEALFLOW_ARTIFACTS"] = os.path.join(self.tmp.name, "art")
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        del os.environ["DEALFLOW_DB"]
        del os.environ["DEALFLOW_ARTIFACTS"]
        self.tmp.cleanup()

    def submit(self, **overrides):
        form = {"name": "Web Deal", "asset_type": "Commercial",
                "location": "Vancouver, WA", "purchase_price": "10000000",
                "noi": "650000", "ltv": "70", "interest_rate": "6.5",
                "amortization_years": "25"}
        form.update(overrides)
        return self.client.post("/intake", data=form)

    def deal_id_from(self, response):
        m = re.search(r"/results/(AT-[0-9-]+)", response.headers["Location"])
        self.assertIsNotNone(m, "expected redirect to results page")
        return m.group(1)

    def test_landing(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Have a deal?", r.data)

    def test_intake_form_renders(self):
        r = self.client.get("/intake")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"purchase_price", r.data)

    def test_valid_submission_redirects_to_results(self):
        r = self.submit()
        self.assertEqual(r.status_code, 302)
        deal_id = self.deal_id_from(r)
        r2 = self.client.get("/results/%s" % deal_id)
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b"Based on the information you provided", r2.data)
        self.assertIn(b"6.50%", r2.data)  # cap rate shown
        self.assertIn(b"not an investment", r2.data)  # disclaimer

    def test_incomplete_optional_information_accepted(self):
        r = self.client.post("/intake", data={
            "name": "Bare Deal", "asset_type": "Land",
            "location": "Ridgefield, WA", "purchase_price": "200000",
            "noi": "12000"})
        self.assertEqual(r.status_code, 302)
        deal_id = self.deal_id_from(r)
        r2 = self.client.get("/results/%s" % deal_id)
        self.assertIn(b"Not computable from your inputs", r2.data)

    def test_invalid_submission_shows_errors(self):
        r = self.submit(purchase_price="-50")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"cannot be negative", r.data)
        self.assertEqual(records.list_deals(), [])

    def test_pipeline_lists_submitted_deal(self):
        deal_id = self.deal_id_from(self.submit())
        r = self.client.get("/pipeline")
        self.assertEqual(r.status_code, 200)
        self.assertIn(deal_id.encode(), r.data)
        self.assertIn(b"Web Deal", r.data)

    def test_detail_opens_and_status_persists(self):
        deal_id = self.deal_id_from(self.submit())
        r = self.client.get("/deals/%s" % deal_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Original inputs", r.data)
        r = self.client.post("/deals/%s/status" % deal_id,
                             data={"status": "REVIEWING"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(records.get_deal(deal_id)["status"], "REVIEWING")
        r = self.client.get("/deals/%s" % deal_id)
        self.assertIn(b"REVIEWING", r.data)

    def test_illegal_status_change_rejected(self):
        deal_id = self.deal_id_from(self.submit())
        r = self.client.post("/deals/%s/status" % deal_id,
                             data={"status": "PURSUE"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(records.get_deal(deal_id)["status"], "NEW")

    def test_excel_download(self):
        deal_id = self.deal_id_from(self.submit())
        r = self.client.get("/deals/%s/excel" % deal_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheet", r.headers["Content-Type"])
        self.assertTrue(r.data.startswith(b"PK"))  # xlsx is a zip
        deal = records.get_deal(deal_id)
        self.assertTrue(deal["artifact_path"].endswith(".xlsx"))

    def test_unknown_deal_404(self):
        self.assertEqual(self.client.get("/deals/AT-2099-000001").status_code, 404)


if __name__ == "__main__":
    unittest.main()
