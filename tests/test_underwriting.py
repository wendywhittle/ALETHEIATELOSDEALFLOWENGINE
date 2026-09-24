"""Underwriting engine tests: direct/derived/missing NOI, financing gaps,
invalid inputs, zero and negative values, determinism, and a full
financed-deal calculation check."""

import unittest

import underwriting
from underwriting import calculate

FULL = {
    "purchase_price": 10_000_000,
    "noi": 650_000,
    "egi": None,
    "operating_expenses": None,
    "occupancy": 95.0,
    "ltv": 70.0,
    "interest_rate": 6.5,
    "amortization_years": 25,
    "hold_period": 5,
    "exit_cap_rate": 6.0,
    "closing_costs": 0.0,
}


class TestNOI(unittest.TestCase):
    def test_direct_noi_takes_precedence(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 650_000})
        self.assertEqual(out["noi"]["value"], 650_000)
        self.assertEqual(out["noi"]["status"], "supplied")
        self.assertEqual(out["noi"]["inputs"], ["noi"])

    def test_explicit_noi_beats_egi_opex(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 700_000,
                         "egi": 1_000_000, "operating_expenses": 350_000})
        self.assertEqual(out["noi"]["value"], 700_000)
        self.assertEqual(out["noi"]["status"], "supplied")

    def test_derived_noi(self):
        out = calculate({"purchase_price": 10_000_000,
                         "egi": 1_000_000, "operating_expenses": 350_000})
        self.assertEqual(out["noi"]["value"], 650_000)
        self.assertEqual(out["noi"]["status"], "calculated")
        self.assertEqual(out["noi"]["inputs"], ["egi", "operating_expenses"])

    def test_derived_noi_may_be_negative(self):
        out = calculate({"purchase_price": 10_000_000,
                         "egi": 300_000, "operating_expenses": 400_000})
        self.assertEqual(out["noi"]["value"], -100_000)
        self.assertEqual(out["noi"]["status"], "calculated")

    def test_missing_noi(self):
        out = calculate({"purchase_price": 10_000_000})
        self.assertIsNone(out["noi"]["value"])
        self.assertEqual(out["noi"]["status"], "unavailable")

    def test_partially_derivable_noi(self):
        out = calculate({"purchase_price": 10_000_000, "egi": 1_000_000})
        self.assertIsNone(out["noi"]["value"])
        self.assertEqual(out["noi"]["status"], "unavailable")

    def test_negative_supplied_noi_is_not_used(self):
        out = calculate({"purchase_price": 10_000_000, "noi": -5})
        self.assertIsNone(out["noi"]["value"])
        self.assertEqual(out["noi"]["status"], "unavailable")


class TestMissingFinancing(unittest.TestCase):
    def test_no_ltv_means_unknown_financing_not_all_cash(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 650_000})
        self.assertIsNone(out["loan_amount"]["value"])
        self.assertEqual(out["loan_amount"]["status"], "unavailable")
        self.assertIsNone(out["initial_equity"]["value"])
        self.assertEqual(out["initial_equity"]["status"], "unavailable")
        self.assertIsNone(out["debt_service"]["value"])
        # cap rate still works: it needs no financing
        self.assertAlmostEqual(out["cap_rate"]["value"], 0.065)

    def test_missing_rate_blocks_debt_service(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 650_000,
                         "ltv": 70, "amortization_years": 25})
        self.assertEqual(out["loan_amount"]["value"], 7_000_000)
        self.assertIsNone(out["debt_service"]["value"])
        self.assertIsNone(out["dscr"]["value"])
        self.assertIsNone(out["cash_flow"]["value"])


class TestInvalidAndZero(unittest.TestCase):
    def test_negative_price_never_crashes(self):
        out = calculate({"purchase_price": -100, "noi": 650_000})
        self.assertIsNone(out["cap_rate"]["value"])
        self.assertEqual(out["cap_rate"]["status"], "unavailable")
        self.assertIsNone(out["loan_amount"]["value"])

    def test_negative_ltv(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 650_000,
                         "ltv": -10})
        self.assertIsNone(out["loan_amount"]["value"])
        self.assertEqual(out["loan_amount"]["status"], "unavailable")

    def test_zero_price_cap_rate_is_unknown_not_zero(self):
        out = calculate({"purchase_price": 0, "noi": 650_000})
        self.assertIsNone(out["cap_rate"]["value"])
        self.assertEqual(out["cap_rate"]["status"], "unknown")

    def test_zero_ltv_is_real_all_cash(self):
        out = calculate({"purchase_price": 10_000_000, "noi": 650_000,
                         "ltv": 0})
        self.assertEqual(out["loan_amount"]["value"], 0)
        self.assertEqual(out["loan_amount"]["status"], "calculated")
        self.assertEqual(out["debt_service"]["value"], 0)
        self.assertEqual(out["initial_equity"]["value"], 10_000_000)
        self.assertEqual(out["dscr"]["status"], "unknown")  # no debt: N/A
        self.assertEqual(out["cash_flow"]["value"], 650_000)

    def test_inputs_never_mutated(self):
        src = dict(FULL)
        snapshot = dict(src)
        calculate(src)
        self.assertEqual(src, snapshot)


class TestFullDeal(unittest.TestCase):
    def test_financed_deal_numbers(self):
        out = calculate(FULL)
        self.assertAlmostEqual(out["cap_rate"]["value"], 0.065)
        self.assertAlmostEqual(out["loan_amount"]["value"], 7_000_000)
        self.assertAlmostEqual(out["initial_equity"]["value"], 3_000_000)
        # ~$47,266/mo * 12
        self.assertAlmostEqual(out["debt_service"]["value"], 567_191, delta=500)
        self.assertAlmostEqual(out["dscr"]["value"], 650_000 / out["debt_service"]["value"])
        self.assertAlmostEqual(out["cash_flow"]["value"],
                               650_000 - out["debt_service"]["value"])
        self.assertAlmostEqual(out["cash_on_cash"]["value"],
                               out["cash_flow"]["value"] / 3_000_000)
        self.assertAlmostEqual(out["exit_value"]["value"], 650_000 / 0.06)
        self.assertEqual(out["exit_value"]["status"], "calculated")
        # IRR ≈ 10.8%, equity multiple ≈ 1.64
        self.assertGreater(out["irr"]["value"], 0.10)
        self.assertLess(out["irr"]["value"], 0.12)
        self.assertAlmostEqual(out["equity_multiple"]["value"], 1.636, delta=0.02)

    def test_every_metric_has_provenance(self):
        out = calculate(FULL)
        for key, m in out.items():
            self.assertIn(m["status"],
                          ("supplied", "calculated", "unavailable", "unknown"),
                          key)
            self.assertIsInstance(m["inputs"], list, key)
            if m["status"] in ("calculated", "supplied"):
                self.assertIsNotNone(m["value"], key)
            else:
                self.assertIsNone(m["value"], key)

    def test_deterministic(self):
        first = calculate(FULL)
        second = calculate(FULL)
        self.assertEqual(first, second)
        # order-independent input construction still matches
        third = calculate({k: FULL[k] for k in reversed(list(FULL))})
        self.assertEqual(first, third)


class TestEGIDerivation(unittest.TestCase):
    def test_full_chain_scheduled_to_egi_to_noi_to_cap_rate(self):
        out = calculate({"purchase_price": 1_000_000,
                         "scheduled_rent_annual": 100_000,
                         "vacancy_loss_annual": 5_000,
                         "other_income_annual": 2_000,
                         "operating_expenses": 30_000})
        self.assertEqual(out["egi"]["value"], 97_000)
        self.assertEqual(out["egi"]["status"], "calculated")
        self.assertEqual(out["egi"]["inputs"],
                         ["scheduled_rent_annual", "vacancy_loss_annual",
                          "other_income_annual"])
        self.assertEqual(out["noi"]["value"], 67_000)
        self.assertEqual(out["noi"]["status"], "calculated")
        self.assertEqual(out["noi"]["inputs"], ["egi", "operating_expenses"])
        self.assertAlmostEqual(out["cap_rate"]["value"], 0.067)

    def test_explicit_egi_beats_derived_egi(self):
        out = calculate({"purchase_price": 1_000_000, "egi": 90_000,
                         "scheduled_rent_annual": 100_000,
                         "vacancy_loss_annual": 5_000,
                         "other_income_annual": 2_000,
                         "operating_expenses": 30_000})
        self.assertEqual(out["egi"]["value"], 90_000)
        self.assertEqual(out["egi"]["status"], "supplied")
        self.assertEqual(out["egi"]["inputs"], ["egi"])
        self.assertEqual(out["noi"]["value"], 60_000)

    def test_explicit_noi_beats_everything(self):
        out = calculate({"purchase_price": 1_000_000, "noi": 50_000,
                         "egi": 90_000,
                         "scheduled_rent_annual": 100_000,
                         "vacancy_loss_annual": 5_000,
                         "other_income_annual": 2_000,
                         "operating_expenses": 30_000})
        self.assertEqual(out["noi"]["value"], 50_000)
        self.assertEqual(out["noi"]["status"], "supplied")

    def test_partial_components_egi_unavailable(self):
        out = calculate({"purchase_price": 1_000_000,
                         "scheduled_rent_annual": 100_000,
                         "vacancy_loss_annual": 5_000,
                         "operating_expenses": 30_000})
        self.assertIsNone(out["egi"]["value"])
        self.assertEqual(out["egi"]["status"], "unavailable")
        self.assertIsNone(out["noi"]["value"])
        self.assertEqual(out["noi"]["status"], "unavailable")

    def test_no_rent_detail_no_egi_egi_unavailable(self):
        out = calculate({"purchase_price": 1_000_000,
                         "operating_expenses": 30_000})
        self.assertIsNone(out["egi"]["value"])
        self.assertEqual(out["egi"]["status"], "unavailable")

    def test_negative_supplied_egi_is_not_used(self):
        out = calculate({"purchase_price": 1_000_000, "egi": -100,
                         "operating_expenses": 30_000})
        self.assertIsNone(out["egi"]["value"])
        self.assertEqual(out["egi"]["status"], "unavailable")
        self.assertIsNone(out["noi"]["value"])

    def test_egi_determinism(self):
        src = {"purchase_price": 1_000_000,
               "scheduled_rent_annual": 100_000,
               "vacancy_loss_annual": 5_000,
               "other_income_annual": 2_000,
               "operating_expenses": 30_000}
        self.assertEqual(calculate(src), calculate(dict(src)))


if __name__ == "__main__":
    unittest.main()
