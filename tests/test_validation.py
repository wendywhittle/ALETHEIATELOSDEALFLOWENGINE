"""Validation tests: required-missing vs optional-missing vs invalid vs valid."""

import unittest

import validation
from validation import validate


def base(**overrides):
    raw = {"name": "Test Deal", "asset_type": "Multi-family",
           "location": "Vancouver, WA", "purchase_price": "1000000",
           "noi": "65000"}
    raw.update(overrides)
    return raw


class TestValidation(unittest.TestCase):
    def test_valid_full_submission(self):
        cleaned, errors = validate(base(ltv="70", interest_rate="6.5",
                                        amortization_years="25"))
        self.assertEqual(errors, [])
        self.assertEqual(cleaned["purchase_price"], 1_000_000.0)
        self.assertEqual(cleaned["ltv"], 70.0)

    def test_incomplete_optional_information_is_fine(self):
        cleaned, errors = validate(base())
        self.assertEqual(errors, [])
        for f in ("egi", "operating_expenses", "occupancy", "ltv",
                  "interest_rate", "amortization_years", "hold_period",
                  "exit_cap_rate", "closing_costs"):
            self.assertIsNone(cleaned[f], f)

    def test_required_missing(self):
        cleaned, errors = validate(base(name="  "))
        codes = {(e["field"], e["code"]) for e in errors}
        self.assertIn(("name", "required_missing"), codes)

    def test_noi_derivable_from_egi_and_opex(self):
        cleaned, errors = validate(base(noi="", egi="100000",
                                        operating_expenses="35000"))
        self.assertEqual(errors, [])
        self.assertIsNone(cleaned["noi"])

    def test_missing_noi_is_valid_state(self):
        # Missing NOI is allowed: dependent metrics report as unavailable
        # (README underwriting rule; "missing NOI" is in the README test list).
        cleaned, errors = validate(base(noi="", egi="", operating_expenses=""))
        self.assertEqual(errors, [])
        self.assertIsNone(cleaned["noi"])

    def test_invalid_values(self):
        cleaned, errors = validate(base(purchase_price="-5", ltv="150",
                                        interest_rate="-1",
                                        amortization_years="0",
                                        occupancy="101"))
        by_field = {}
        for e in errors:
            by_field.setdefault(e["field"], []).append(e["code"])
        for f in ("purchase_price", "ltv", "interest_rate",
                  "amortization_years", "occupancy"):
            self.assertIn("invalid", by_field.get(f, []), f)

    def test_non_numeric_rejected(self):
        cleaned, errors = validate(base(purchase_price="a lot"))
        self.assertTrue(any(e["field"] == "purchase_price"
                            and e["code"] == "invalid" for e in errors))

    def test_field_state_distinguishes_optional_missing(self):
        cleaned, errors = validate(base())
        self.assertEqual(errors, [])
        self.assertEqual(validation.field_state("ltv", cleaned),
                         "optional_missing")
        self.assertEqual(validation.field_state("purchase_price", cleaned),
                         "valid")

    def test_rental_income_detail_optional(self):
        cleaned, errors = validate(base(scheduled_rent_annual="100000",
                                        vacancy_loss_annual="5000",
                                        other_income_annual="2000"))
        self.assertEqual(errors, [])
        self.assertEqual(cleaned["scheduled_rent_annual"], 100_000.0)
        self.assertEqual(cleaned["vacancy_loss_annual"], 5_000.0)
        self.assertEqual(cleaned["other_income_annual"], 2_000.0)

    def test_rental_income_detail_negative_rejected(self):
        for field in ("scheduled_rent_annual", "vacancy_loss_annual",
                      "other_income_annual"):
            cleaned, errors = validate(base(**{field: "-1"}))
            codes = {(e["field"], e["code"]) for e in errors}
            self.assertIn((field, "invalid"), codes)


if __name__ == "__main__":
    unittest.main()
