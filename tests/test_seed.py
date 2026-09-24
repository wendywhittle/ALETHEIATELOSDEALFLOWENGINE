"""Seed tests: the 131 curated deals restore on boot, idempotently, with the
home-run triage applied and listed cap rates kept as evidence only."""

import os
import tempfile
import unittest

import records
import seed

PURSUE_NAMES = {
    "Nicollet Hotel | 122 S Minnesota Ave | 25 Unit Apartment Building",
    "11.65% CAP Owner Financing | 2068 US-76 | 36 Unit Mobile Home Park",
    "Multi-Family Trailer Park For Sale | 30263 Eden Church Rd | 18 Unit",
    "Beechwood Estates | 501 32nd Street | 20 Unit Mobile Home Park",
}


class SeedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DEALFLOW_DB"] = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        del os.environ["DEALFLOW_DB"]
        self.tmp.cleanup()

    def test_seed_inserts_131_deals(self):
        inserted = seed.seed_database()
        self.assertEqual(len(inserted), 131)
        self.assertEqual(len(records.list_deals()), 131)
        # deterministic order: home runs first
        first_four = [records.get_deal(d)["name"] for d in inserted[:4]]
        self.assertEqual(set(first_four), PURSUE_NAMES)

    def test_home_runs_pursue_rest_new(self):
        seed.seed_database()
        pursue = records.list_deals(status="PURSUE")
        self.assertEqual({d["name"] for d in pursue}, PURSUE_NAMES)
        self.assertEqual(len(records.list_deals(status="NEW")), 127)

    def test_seed_is_idempotent(self):
        seed.seed_database()
        self.assertEqual(seed.seed_database(), [])
        self.assertEqual(len(records.list_deals()), 131)
        self.assertEqual(len(records.list_deals(status="PURSUE")), 4)

    def test_skips_preexisting_source_url(self):
        first = seed.SEED_DEALS[0]
        records.submit_deal({
            "name": "A different name for the same listing",
            "asset_type": first["asset_type"],
            "location": first["location"],
            "purchase_price": first["purchase_price"],
            "source": first["source"],
            "source_url": first["source_url"],
            "notes": first["notes"],
        })
        inserted = seed.seed_database()
        self.assertEqual(len(inserted), 130)
        self.assertEqual(len(records.list_deals()), 131)

    def test_skips_preexisting_name_and_location(self):
        first = seed.SEED_DEALS[0]
        records.submit_deal({
            "name": first["name"],
            "asset_type": first["asset_type"],
            "location": first["location"].upper(),  # case-insensitive
            "purchase_price": first["purchase_price"],
            "source": first["source"],
            "source_url": "",
            "notes": first["notes"],
        })
        inserted = seed.seed_database()
        self.assertEqual(len(inserted), 130)
        self.assertEqual(len(records.list_deals()), 131)

    def test_listed_cap_is_evidence_not_noi(self):
        seed.seed_database()
        for deal in records.list_deals():
            # NOI is never derived from a listed cap rate: it stays missing
            self.assertIsNone(deal["inputs"]["noi"])
            self.assertEqual(
                deal["underwriting"]["cap_rate"]["status"], "unavailable")
        nicollet = [d for d in records.list_deals()
                    if d["name"] in PURSUE_NAMES
                    and "Nicollet" in d["name"]][0]
        self.assertIn("9.92%", nicollet["contact"]["notes"])
        self.assertIn("+342 bps", nicollet["contact"]["notes"])

    def test_fsbo_bank_owned_seed_as_new_without_noi(self):
        seed.seed_database()
        extra = [d for d in records.list_deals()
                 if d["asset_type"] in ("sfh", "bank-owned")]
        self.assertEqual(len(extra), 88)
        for deal in extra:
            self.assertEqual(deal["status"], "NEW")
            self.assertIsNone(deal["inputs"]["noi"])
            self.assertEqual(
                deal["underwriting"]["cap_rate"]["status"], "unavailable")

    def test_dd_checklist_seeded_for_seed_deals(self):
        seed.seed_database()
        deal = records.list_deals()[0]
        self.assertGreater(len(records.list_dd_items(deal["deal_id"])), 0)


if __name__ == "__main__":
    unittest.main()
