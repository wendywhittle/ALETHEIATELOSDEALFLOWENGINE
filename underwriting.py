"""AletheiaTelos DealFlow Engine — deterministic underwriting layer.

Pure calculation:  calculate(deal_inputs) -> {metric: result}

Every metric result is a dict::

    {"value": float | None,
     "status": "supplied" | "calculated" | "unavailable" | "unknown",
     "inputs": [field names actually used],
     "reason": str | None}

Status meanings:
  supplied    - the value came straight from the submitted deal (only NOI can be supplied)
  calculated  - derived deterministically from supplied inputs
  unavailable - cannot be computed because required inputs are missing/invalid
  unknown     - the metric does not apply / is indeterminate (e.g. division by zero,
                DSCR with no debt). Never a silent zero.

Rules (from README / ARCHITECTURE):
  1. Explicit NOI takes precedence.
  2. If NOI is absent but EGI and operating expenses are both supplied,
     NOI = EGI - operating expenses.
  3. Purchase price comes from the submitted deal.
  4. Never invent missing assumptions (no assumed all-cash, no assumed rates).
  5. Missing values never silently become zero.
  6. Identical inputs always produce identical outputs (pure math, no randomness,
     no timestamps, no I/O).
  7. Original inputs are never modified.

Documented modelling choices (no hidden assumptions):
  - LTV is a percentage (0-100). Missing LTV means financing is unknown;
    the engine does NOT assume all-cash.
  - Debt service uses standard monthly amortization.
  - Exit value = NOI / exit cap rate (current NOI; no growth invented).
  - IRR / equity multiple treat the hold period in whole years and assume the
    annual cash flow repeats each year (it is the only cash flow derivable from
    the inputs). Sale proceeds = exit value - remaining loan balance.
"""

import math

SUPPLIED = "supplied"
CALCULATED = "calculated"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"


def _num(value):
    """Coerce to a finite float, or None if missing/invalid. Never raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _metric(value, status, inputs, reason=None):
    return {
        "value": value,
        "status": status,
        "inputs": list(inputs),
        "reason": reason,
    }


def _amortized_payment(principal, annual_rate_pct, amort_years):
    """Monthly amortizing payment for principal at annual_rate_pct over amort_years."""
    n = int(round(amort_years * 12))
    if n <= 0:
        return None
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return principal / n
    return principal * r / (1.0 - (1.0 + r) ** (-n))


def _loan_balance(principal, annual_rate_pct, amort_years, elapsed_years):
    """Remaining balance after elapsed_years of scheduled payments."""
    if principal == 0:
        return 0.0
    n_total = int(round(amort_years * 12))
    k = int(round(elapsed_years * 12))
    r = annual_rate_pct / 100.0 / 12.0
    pmt = _amortized_payment(principal, annual_rate_pct, amort_years)
    if r == 0:
        return max(0.0, principal - pmt * k)
    grown = principal * (1.0 + r) ** k
    paid = pmt * ((1.0 + r) ** k - 1.0) / r
    return max(0.0, grown - paid)


def _irr(cash_flows):
    """Annual IRR via bisection. Returns float or None if it does not converge."""
    def npv(rate):
        total = 0.0
        for t, cf in enumerate(cash_flows):
            total += cf / ((1.0 + rate) ** t)
        return total

    lo, hi = -0.9999, 10.0
    npv_lo, npv_hi = npv(lo), npv(hi)
    if npv_lo == 0.0:
        return lo
    if npv_lo * npv_hi > 0:
        return None  # no sign change: no meaningful IRR
    for _ in range(200):
        mid = (lo + hi) / 2.0
        npv_mid = npv(mid)
        if abs(npv_mid) < 1e-9:
            return mid
        if npv_lo * npv_mid <= 0:
            hi, npv_hi = mid, npv_mid
        else:
            lo, npv_lo = mid, npv_mid
    return (lo + hi) / 2.0


def calculate(inputs):
    """Run the full underwriting calculation on validated deal inputs.

    `inputs` maps field names to numbers/None. It is never modified.
    Returns an ordered dict of metric results with provenance.
    """
    src = dict(inputs)  # read-only copy; caller's dict is never touched

    price = _num(src.get("purchase_price"))
    noi_in = _num(src.get("noi"))
    egi_in = _num(src.get("egi"))
    sched = _num(src.get("scheduled_rent_annual"))
    vac = _num(src.get("vacancy_loss_annual"))
    other = _num(src.get("other_income_annual"))
    opex = _num(src.get("operating_expenses"))
    ltv = _num(src.get("ltv"))
    rate = _num(src.get("interest_rate"))
    amort = _num(src.get("amortization_years"))
    hold = _num(src.get("hold_period"))
    exit_cap = _num(src.get("exit_cap_rate"))
    closing = _num(src.get("closing_costs"))

    price_ok = price is not None and price >= 0

    out = {}

    # ---- EGI: explicit wins; else derive from rent detail; else unavailable ----
    rent_inputs = ["scheduled_rent_annual", "vacancy_loss_annual",
                   "other_income_annual"]
    rent_vals = {"scheduled_rent_annual": sched,
                 "vacancy_loss_annual": vac,
                 "other_income_annual": other}
    rent_ok = all(v is not None and v >= 0 for v in rent_vals.values())
    if egi_in is not None and egi_in >= 0:
        out["egi"] = _metric(egi_in, SUPPLIED, ["egi"])
    elif egi_in is not None:
        out["egi"] = _metric(None, UNAVAILABLE, ["egi"],
                             "invalid input: supplied EGI is negative")
    elif rent_ok:
        out["egi"] = _metric(sched - vac + other, CALCULATED, rent_inputs,
                             "scheduled rent - vacancy loss + other income")
    else:
        present = [f for f in rent_inputs if rent_vals[f] is not None]
        out["egi"] = _metric(None, UNAVAILABLE, present,
                             "EGI derivation needs all three: scheduled rent, "
                             "vacancy loss, and other income")
    egi = out["egi"]["value"]  # None unless supplied or derived

    # ---- NOI: explicit wins; else EGI - opex; else unavailable ----
    if noi_in is not None and noi_in >= 0:
        out["noi"] = _metric(noi_in, SUPPLIED, ["noi"])
    elif noi_in is not None:
        out["noi"] = _metric(None, UNAVAILABLE, ["noi"],
                             "invalid input: supplied NOI is negative")
    elif (egi is not None and egi >= 0
          and opex is not None and opex >= 0):
        out["noi"] = _metric(egi - opex, CALCULATED,
                             ["egi", "operating_expenses"])
    elif egi is None and opex is None:
        out["noi"] = _metric(None, UNAVAILABLE, [],
                             "NOI not supplied and EGI / operating expenses not supplied")
    else:
        present = [f for f, v in (("egi", egi), ("operating_expenses", opex))
                   if v is not None]
        out["noi"] = _metric(None, UNAVAILABLE, present,
                             "NOI derivation needs both EGI and operating expenses")
    noi = out["noi"]["value"]  # None unless supplied or derived
    noi_ok = noi is not None

    # ---- Cap rate ----
    if noi_ok and price_ok and price > 0:
        out["cap_rate"] = _metric(noi / price, CALCULATED,
                                 ["purchase_price", "noi"])
    elif noi_ok and price_ok and price == 0:
        out["cap_rate"] = _metric(None, UNKNOWN, ["purchase_price", "noi"],
                                 "purchase price is zero; cap rate indeterminate")
    elif not price_ok:
        out["cap_rate"] = _metric(None, UNAVAILABLE, ["purchase_price"],
                                 "purchase price missing or invalid")
    else:
        out["cap_rate"] = _metric(None, UNAVAILABLE, ["noi"],
                                 "NOI unavailable")

    # ---- Loan amount ----
    if not price_ok:
        out["loan_amount"] = _metric(None, UNAVAILABLE, ["purchase_price"],
                                    "purchase price missing or invalid")
    elif ltv is None:
        out["loan_amount"] = _metric(None, UNAVAILABLE, [],
                                    "LTV not supplied; financing terms unknown "
                                    "(not assumed to be all-cash)")
    elif ltv < 0 or ltv > 100:
        out["loan_amount"] = _metric(None, UNAVAILABLE, ["ltv"],
                                    "invalid input: LTV outside 0-100")
    else:
        out["loan_amount"] = _metric(price * ltv / 100.0, CALCULATED,
                                    ["purchase_price", "ltv"])
    loan = out["loan_amount"]["value"]
    loan_ok = loan is not None

    # ---- Initial equity ----
    closing_ok = closing is None or closing >= 0
    if not price_ok:
        out["initial_equity"] = _metric(None, UNAVAILABLE, ["purchase_price"],
                                       "purchase price missing or invalid")
    elif not loan_ok:
        out["initial_equity"] = _metric(None, UNAVAILABLE,
                                       out["loan_amount"]["inputs"] or ["ltv"],
                                       "loan amount unavailable; equity not assumed")
    elif not closing_ok:
        out["initial_equity"] = _metric(None, UNAVAILABLE, ["closing_costs"],
                                       "invalid input: closing costs negative")
    else:
        used = ["purchase_price", "ltv"] + (["closing_costs"] if closing is not None else [])
        out["initial_equity"] = _metric(price - loan + (closing or 0.0),
                                       CALCULATED, used)
    equity = out["initial_equity"]["value"]
    equity_ok = equity is not None

    # ---- Annual debt service ----
    if not loan_ok:
        out["debt_service"] = _metric(None, UNAVAILABLE,
                                     out["loan_amount"]["inputs"] or ["ltv"],
                                     "loan amount unavailable")
    elif loan == 0:
        out["debt_service"] = _metric(0.0, CALCULATED, ["purchase_price", "ltv"],
                                     "no debt (LTV 0%)")
    elif rate is None or rate < 0 or amort is None or amort <= 0:
        missing = [f for f, v in (("interest_rate", rate), ("amortization_years", amort))
                   if v is None or v < 0 or (f == "amortization_years" and v <= 0)]
        out["debt_service"] = _metric(None, UNAVAILABLE, missing,
                                     "interest rate / amortization missing or invalid")
    else:
        pmt = _amortized_payment(loan, rate, amort)
        out["debt_service"] = _metric(pmt * 12.0, CALCULATED,
                                     ["loan_amount", "interest_rate",
                                      "amortization_years"])
    ds = out["debt_service"]["value"]
    ds_ok = ds is not None

    # ---- DSCR ----
    if not noi_ok:
        out["dscr"] = _metric(None, UNAVAILABLE, ["noi"], "NOI unavailable")
    elif not ds_ok:
        out["dscr"] = _metric(None, UNAVAILABLE, ["debt_service"],
                             "debt service unavailable")
    elif ds == 0:
        out["dscr"] = _metric(None, UNKNOWN, ["debt_service"],
                             "no debt service; DSCR not applicable")
    else:
        out["dscr"] = _metric(noi / ds, CALCULATED, ["noi", "debt_service"])

    # ---- Annual cash flow ----
    if noi_ok and ds_ok:
        out["cash_flow"] = _metric(noi - ds, CALCULATED, ["noi", "debt_service"])
    else:
        missing = [f for f, ok in (("noi", noi_ok), ("debt_service", ds_ok)) if not ok]
        out["cash_flow"] = _metric(None, UNAVAILABLE, missing,
                                  "needs NOI and debt service")
    cf = out["cash_flow"]["value"]
    cf_ok = cf is not None

    # ---- Cash-on-cash ----
    if cf_ok and equity_ok and equity > 0:
        out["cash_on_cash"] = _metric(cf / equity, CALCULATED,
                                      ["cash_flow", "initial_equity"])
    elif cf_ok and equity_ok and equity == 0:
        out["cash_on_cash"] = _metric(None, UNKNOWN,
                                      ["cash_flow", "initial_equity"],
                                      "initial equity is zero; return indeterminate")
    else:
        missing = [f for f, ok in (("cash_flow", cf_ok), ("initial_equity", equity_ok))
                   if not ok]
        out["cash_on_cash"] = _metric(None, UNAVAILABLE, missing,
                                     "needs cash flow and initial equity")

    # ---- Exit value (current NOI, no growth invented) ----
    if not noi_ok:
        out["exit_value"] = _metric(None, UNAVAILABLE, ["noi"], "NOI unavailable")
    elif exit_cap is None:
        out["exit_value"] = _metric(None, UNAVAILABLE, [],
                                   "exit cap rate not supplied")
    elif exit_cap <= 0:
        out["exit_value"] = _metric(None, UNKNOWN, ["exit_cap_rate"],
                                   "exit cap rate must be positive")
    else:
        out["exit_value"] = _metric(noi / (exit_cap / 100.0), CALCULATED,
                                   ["noi", "exit_cap_rate"],
                                   "uses current NOI; no growth assumed")
    exit_val = out["exit_value"]["value"]
    exit_ok = exit_val is not None

    # ---- IRR & equity multiple ----
    hold_ok = hold is not None and hold >= 1
    years = int(hold) if hold_ok else None  # whole years; documented above
    financing_for_exit_ok = loan_ok and (loan == 0 or (
        rate is not None and rate >= 0 and amort is not None and amort > 0))

    irr_inputs = ["initial_equity", "cash_flow", "exit_value", "hold_period"]
    if loan_ok and loan > 0:
        irr_inputs += ["loan_amount", "interest_rate", "amortization_years"]

    if not (equity_ok and equity > 0):
        out["irr"] = _metric(None, UNAVAILABLE, ["initial_equity"],
                            "needs positive initial equity")
        out["equity_multiple"] = _metric(None, UNAVAILABLE, ["initial_equity"],
                                        "needs positive initial equity")
    elif not cf_ok:
        out["irr"] = _metric(None, UNAVAILABLE, ["cash_flow"],
                            "cash flow unavailable")
        out["equity_multiple"] = _metric(None, UNAVAILABLE, ["cash_flow"],
                                        "cash flow unavailable")
    elif not exit_ok:
        out["irr"] = _metric(None, UNAVAILABLE, ["exit_value"],
                            "exit value unavailable")
        out["equity_multiple"] = _metric(None, UNAVAILABLE, ["exit_value"],
                                        "exit value unavailable")
    elif not hold_ok:
        out["irr"] = _metric(None, UNAVAILABLE, [], "hold period not supplied")
        out["equity_multiple"] = _metric(None, UNAVAILABLE, [],
                                        "hold period not supplied")
    elif not financing_for_exit_ok:
        out["irr"] = _metric(None, UNAVAILABLE, irr_inputs,
                            "loan payoff at exit cannot be determined")
        out["equity_multiple"] = _metric(None, UNAVAILABLE, irr_inputs,
                                        "loan payoff at exit cannot be determined")
    else:
        balance = _loan_balance(loan, rate or 0.0, amort or 1.0, years)
        sale_proceeds = exit_val - balance
        flows = [-equity] + [cf] * (years - 1) + [cf + sale_proceeds]
        irr_val = _irr(flows)
        total_in = years * cf + sale_proceeds
        if irr_val is None:
            out["irr"] = _metric(None, UNKNOWN, irr_inputs,
                                "cash flows have no meaningful IRR")
        else:
            out["irr"] = _metric(irr_val, CALCULATED, irr_inputs)
        out["equity_multiple"] = _metric(total_in / equity, CALCULATED,
                                         irr_inputs)

    return out
