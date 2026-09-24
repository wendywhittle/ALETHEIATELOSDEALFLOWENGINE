"""Deal record tests: unique IDs, input preservation, explicit missing
information, persistence, status transitions."""

import datetime
import os
import re
import tempfile
import unittest

import records


class RecordsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DEALFLOW_DB"] = os.path.join(self.tmp.name, "test.db")
        self.year = datetime.datetime.now().year

    def tearDown(self):
        del os.environ["DEALFLOW_DB"]
        self.tmp.cleanup()

    def sample(self, **overrides):
        raw = {"name": "Sample Deal", "asset_type": "Commercial",
               "location": "Vancouver, WA", "purchase_price": 10_000_000,
               "noi": 650_000}
        raw.update(overrides)
        return raw

    def test_unique_deal_ids(self):
        a = records.submit_deal(self.sample())
        b = records.submit_deal(self.sample(name="Second Deal"))
        self.assertNotEqual(a, b)
        pat = re.compile(r"^AT-%d-\d{6}$" % self.year)
        self.assertRegex(a, pat)
        self.assertRegex(b, pat)
        # sequential, never derived from property data
        self.assertLess(int(a.rsplit("-", 1)[1]), int(b.rsplit("-", 1)[1]))

    def test_original_inputs_preserved(self):
        deal_id = records.submit_deal(
            self.sample(ltv=70, interest_rate=6.5, contact_name="Wendy"))
        deal = records.get_deal(deal_id)
        self.assertEqual(deal["inputs"]["purchase_price"], 10_000_000)
        self.assertEqual(deal["inputs"]["noi"], 650_000)
        self.assertEqual(deal["inputs"]["ltv"], 70)
        self.assertIsNone(deal["inputs"]["egi"])
        self.assertEqual(deal["contact"]["contact_name"], "Wendy")
        self.assertEqual(deal["status"], "NEW")

    def test_missing_information_explicit(self):
        deal_id = records.submit_deal(self.sample())
        deal = records.get_deal(deal_id)
        self.assertEqual(deal["underwriting"]["loan_amount"]["status"],
                         "unavailable")
        self.assertIsNone(deal["underwriting"]["loan_amount"]["value"])
        self.assertEqual(deal["underwriting"]["cap_rate"]["status"],
                         "calculated")

    def test_calculations_stored_not_recomputed_on_read(self):
        deal_id = records.submit_deal(self.sample())
        first = records.get_deal(deal_id)["underwriting"]
        second = records.get_deal(deal_id)["underwriting"]
        self.assertEqual(first, second)

    def test_persistence_across_connections(self):
        deal_id = records.submit_deal(self.sample())
        # fresh read path, new connection each call
        deal = records.get_deal(deal_id)
        self.assertEqual(deal["deal_id"], deal_id)
        self.assertEqual(len(records.list_deals()), 1)

    def test_invalid_submission_rejected(self):
        with self.assertRaises(ValueError):
            records.submit_deal(self.sample(purchase_price=-100))

    def test_status_transitions(self):
        deal_id = records.submit_deal(self.sample())
        records.set_status(deal_id, "REVIEWING")
        self.assertEqual(records.get_deal(deal_id)["status"], "REVIEWING")
        records.set_status(deal_id, "PURSUE")
        self.assertEqual(records.get_deal(deal_id)["status"], "PURSUE")

    def test_illegal_transition_rejected(self):
        deal_id = records.submit_deal(self.sample())
        with self.assertRaises(ValueError):
            records.set_status(deal_id, "PURSUE")  # NEW -> PURSUE skips REVIEWING

    def test_unknown_deal(self):
        self.assertIsNone(records.get_deal("AT-2099-999999"))

    def test_source_stored_and_optional(self):
        deal_id = records.submit_deal(self.sample(source="LoopNet"))
        self.assertEqual(records.get_deal(deal_id)["source"], "LoopNet")
        deal_id2 = records.submit_deal(self.sample(name="No Source Deal"))
        self.assertEqual(records.get_deal(deal_id2)["source"], "")
        listed = {d["deal_id"]: d["source"] for d in records.list_deals()}
        self.assertEqual(listed[deal_id], "LoopNet")
        self.assertEqual(listed[deal_id2], "")


if __name__ == "__main__":
    unittest.main()
