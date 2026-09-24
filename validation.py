"""AletheiaTelos DealFlow Engine — intake validation.

validate(raw) -> (cleaned, errors)

`cleaned`  : dict with every known field; numbers as floats, missing as None,
             strings stripped. Always returned, even when errors exist.
`errors`   : list of {"field", "code", "message"} where code is one of
             "required_missing" | "invalid".

Distinctions (per ARCHITECTURE §12):
  required field missing  -> error, code "required_missing"
  optional field missing  -> no error; cleaned value is None
  invalid value           -> error, code "invalid"
  valid value             -> no error

Note on NOI: the intake spec lists Annual NOI as core information, but the
underwriting rules (README: explicit NOI wins; derive only from EGI minus
operating expenses; missing values stay missing; the README's own test list
includes "missing NOI") require a deal to be recordable without NOI. NOI is
therefore optional at intake: when absent, metrics that need it report as
unavailable. Supplying both EGI and operating expenses lets NOI be derived.
"""

IDENTITY_FIELDS = ["name", "asset_type", "location"]
CONTACT_FIELDS = ["contact_name", "contact_email", "contact_phone"]
EXTRA_FIELDS = ["source_url", "notes"]

# All numeric intake fields, in canonical order.
NUMERIC_FIELDS = [
    "purchase_price",
    "noi",
    "egi",
    "scheduled_rent_annual",
    "vacancy_loss_annual",
    "other_income_annual",
    "operating_expenses",
    "occupancy",
    "ltv",
    "interest_rate",
    "amortization_years",
    "hold_period",
    "exit_cap_rate",
    "closing_costs",
]

# Numeric fields that live in the canonical "original_inputs" block.
INPUT_FIELDS = list(NUMERIC_FIELDS)


def _coerce_number(raw_value):
    """Return (value_or_None, is_invalid). Blank/None -> (None, False)."""
    if raw_value is None:
        return None, False
    if isinstance(raw_value, bool):
        return None, True
    if isinstance(raw_value, (int, float)):
        return float(raw_value), False
    text = str(raw_value).strip().replace(",", "").replace("$", "")
    if text == "":
        return None, False
    try:
        return float(text), False
    except ValueError:
        return None, True


def validate(raw):
    raw = dict(raw or {})
    errors = []
    cleaned = {}

    # ---- identity (required) ----
    for field in IDENTITY_FIELDS:
        value = raw.get(field)
        text = "" if value is None else str(value).strip()
        if not text:
            errors.append({
                "field": field,
                "code": "required_missing",
                "message": "%s is required." % field.replace("_", " ").capitalize(),
            })
            cleaned[field] = ""
        else:
            cleaned[field] = text

    # ---- source (optional; e.g. Facebook Marketplace, LoopNet, Crexi) ----
    cleaned["source"] = str(raw.get("source") or "").strip()

    # ---- numerics ----
    numeric = {}
    for field in NUMERIC_FIELDS:
        value, invalid = _coerce_number(raw.get(field))
        if invalid:
            errors.append({
                "field": field,
                "code": "invalid",
                "message": "%s must be a number." % field.replace("_", " "),
            })
        numeric[field] = value
        cleaned[field] = value

    # ---- required core: purchase price ----
    if numeric["purchase_price"] is None:
        errors.append({
            "field": "purchase_price",
            "code": "required_missing",
            "message": "Purchase price is required.",
        })

    # NOI is optional. Missing NOI is a valid state: underwriting derives it
    # from EGI minus operating expenses only when both are supplied, and
    # otherwise reports dependent metrics as unavailable.

    # ---- invalid value rules ----
    def invalid(field, message):
        errors.append({"field": field, "code": "invalid", "message": message})

    p = numeric["purchase_price"]
    if p is not None and p < 0:
        invalid("purchase_price", "Purchase price cannot be negative.")

    n = numeric["noi"]
    if n is not None and n < 0:
        invalid("noi", "Annual NOI cannot be negative.")

    for field in ("egi", "scheduled_rent_annual", "vacancy_loss_annual",
                  "other_income_annual", "operating_expenses", "closing_costs"):
        v = numeric[field]
        if v is not None and v < 0:
            invalid(field, "%s cannot be negative." % field.replace("_", " "))

    occ = numeric["occupancy"]
    if occ is not None and (occ < 0 or occ > 100):
        invalid("occupancy", "Occupancy must be between 0 and 100 percent.")

    ltv = numeric["ltv"]
    if ltv is not None and (ltv < 0 or ltv > 100):
        invalid("ltv", "LTV must be between 0 and 100 percent.")

    rate = numeric["interest_rate"]
    if rate is not None and rate < 0:
        invalid("interest_rate", "Interest rate cannot be negative.")

    amort = numeric["amortization_years"]
    if amort is not None and amort <= 0:
        invalid("amortization_years",
                "Amortization period must be greater than zero.")

    hold = numeric["hold_period"]
    if hold is not None and hold <= 0:
        invalid("hold_period", "Hold period must be greater than zero.")

    xcap = numeric["exit_cap_rate"]
    if xcap is not None and xcap <= 0:
        invalid("exit_cap_rate", "Exit cap rate must be greater than zero.")

    # ---- contact (all optional) ----
    for field in CONTACT_FIELDS:
        value = raw.get(field)
        cleaned[field] = "" if value is None else str(value).strip()
    email = cleaned["contact_email"]
    if email and "@" not in email:
        invalid("contact_email", "Contact email does not look like an email address.")

    # ---- extras (optional, passthrough) ----
    for field in EXTRA_FIELDS:
        value = raw.get(field)
        cleaned[field] = "" if value is None else str(value).strip()

    return cleaned, errors


def field_state(field, cleaned):
    """Classify a validated field: 'valid' or 'optional_missing'.

    (Required-missing and invalid fields never reach here cleanly; they are
    reported in the errors list instead.)
    """
    value = cleaned.get(field)
    if value is None or value == "":
        return "optional_missing"
    return "valid"
