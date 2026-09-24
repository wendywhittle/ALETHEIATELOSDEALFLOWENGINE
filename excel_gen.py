"""AletheiaTelos DealFlow Engine — Excel artifact generator.

generate_excel(deal) -> path to the workbook.

The workbook is generated FROM the canonical deal record and its stored
underwriting result. It is an artifact only: it never feeds data back into
the application and must not become a second source of truth.

Sheets:
  Deal Summary            identity, price, NOI, contact, status, timestamps
  Operating Inputs        EGI, operating expenses, occupancy (+ state)
  Financing               LTV, rate, amortization, loan, equity, debt service (+ state)
  Returns                 cap rate, DSCR, cash flow, CoC, exit value, IRR,
                          equity multiple (+ state)
  Assumptions / Missing Data   every input and metric with its
                          supplied | calculated | unavailable | unknown state
"""

import os

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

BOLD = Font(bold=True, size=12)
TITLE = Font(bold=True, size=14)
HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
MISSING_FONT = Font(italic=True, color="808080")

MONEY_FMT = '"$"#,##0'
MONEY2_FMT = '"$"#,##0.00'
PCT_FMT = '0.00%'
NUM_FMT = '#,##0.00'

# metric key -> (label, format)
METRIC_LABELS = {
    "noi": ("NOI (annual)", MONEY_FMT),
    "egi": ("EGI (annual)", MONEY_FMT),
    "cap_rate": ("Cap rate", PCT_FMT),
    "loan_amount": ("Loan amount", MONEY_FMT),
    "initial_equity": ("Initial equity", MONEY_FMT),
    "debt_service": ("Annual debt service", MONEY_FMT),
    "dscr": ("DSCR", NUM_FMT),
    "cash_flow": ("Annual cash flow", MONEY_FMT),
    "cash_on_cash": ("Cash-on-cash return", PCT_FMT),
    "exit_value": ("Exit value", MONEY_FMT),
    "irr": ("IRR (annual)", PCT_FMT),
    "equity_multiple": ("Equity multiple", NUM_FMT),
}

INPUT_LABELS = {
    "purchase_price": ("Purchase price", MONEY_FMT),
    "noi": ("NOI — supplied (annual)", MONEY_FMT),
    "egi": ("EGI (annual)", MONEY_FMT),
    "scheduled_rent_annual": ("Scheduled rent (annual)", MONEY_FMT),
    "vacancy_loss_annual": ("Vacancy / collection loss (annual)", MONEY_FMT),
    "other_income_annual": ("Other income (annual)", MONEY_FMT),
    "operating_expenses": ("Operating expenses (annual)", MONEY_FMT),
    "occupancy": ("Occupancy", PCT_FMT),
    "ltv": ("LTV", PCT_FMT),
    "interest_rate": ("Interest rate", PCT_FMT),
    "amortization_years": ("Amortization (years)", NUM_FMT),
    "hold_period": ("Hold period (years)", NUM_FMT),
    "exit_cap_rate": ("Exit cap rate", PCT_FMT),
    "closing_costs": ("Closing costs", MONEY_FMT),
}


def _display_value(key, value):
    """Convert stored decimals to display units for percent-style fields."""
    if value is None:
        return None
    if key in ("occupancy", "ltv", "interest_rate", "exit_cap_rate"):
        return value / 100.0
    return value


def _state_cell(ws, row, value, state, reason, fmt):
    if value is None:
        ws.cell(row=row, column=2, value="—").font = MISSING_FONT
    else:
        c = ws.cell(row=row, column=2, value=value)
        c.number_format = fmt
    ws.cell(row=row, column=3, value=state)
    if reason:
        ws.cell(row=row, column=4, value=reason)


def _header(ws, title):
    ws["A1"] = title
    ws["A1"].font = TITLE
    ws["A2"] = "Field"
    ws["B2"] = "Value"
    ws["C2"] = "State"
    ws["D2"] = "Basis / note"
    for col in range(1, 5):
        c = ws.cell(row=2, column=col)
        c.fill = HDR_FILL
        c.font = HDR_FONT
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 52


def generate_excel(deal, out_dir=None):
    """Build the workbook for a canonical deal dict. Returns the file path."""
    out_dir = out_dir or os.environ.get(
        "DEALFLOW_ARTIFACTS", os.path.join(REPO_DIR, "artifacts"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.xlsx" % deal["deal_id"])

    wb = Workbook()
    inputs = deal["inputs"]
    uw = deal["underwriting"]
    contact = deal["contact"]

    # ---- Deal Summary ----
    ws = wb.active
    ws.title = "Deal Summary"
    _header(ws, "Deal Summary — %s" % deal["deal_id"])
    rows = [
        ("Deal ID", deal["deal_id"], None),
        ("Deal name", deal["name"], None),
        ("Asset type", deal["asset_type"], None),
        ("Location", deal["location"], None),
        ("Status", deal["status"], None),
        ("Submitted", deal["created_at"], None),
        ("Last updated", deal["updated_at"], None),
        ("Contact name", contact.get("contact_name") or None, None),
        ("Contact email", contact.get("contact_email") or None, None),
        ("Contact phone", contact.get("contact_phone") or None, None),
    ]
    r = 3
    for label, value, _ in rows:
        ws.cell(row=r, column=1, value=label).font = BOLD
        if value in (None, ""):
            ws.cell(row=r, column=2, value="—").font = MISSING_FONT
            ws.cell(row=r, column=3, value="unavailable")
        else:
            ws.cell(row=r, column=2, value=value)
            ws.cell(row=r, column=3, value="supplied")
        r += 1
    # price + NOI with real provenance
    for key in ("purchase_price",):
        label, fmt = INPUT_LABELS[key]
        ws.cell(row=r, column=1, value=label).font = BOLD
        _state_cell(ws, r, _display_value(key, inputs.get(key)),
                    "supplied" if inputs.get(key) is not None else "unavailable",
                    None, fmt)
        r += 1
    m = uw.get("noi", {})
    ws.cell(row=r, column=1, value="NOI (annual)").font = BOLD
    _state_cell(ws, r, m.get("value"), m.get("status"), m.get("reason"), MONEY_FMT)

    # ---- Operating Inputs ----
    ws = wb.create_sheet("Operating Inputs")
    _header(ws, "Operating Inputs")
    r = 3
    for key in ("scheduled_rent_annual", "vacancy_loss_annual",
                "other_income_annual"):
        label, fmt = INPUT_LABELS[key]
        ws.cell(row=r, column=1, value=label).font = BOLD
        v = inputs.get(key)
        _state_cell(ws, r, _display_value(key, v),
                    "supplied" if v is not None else "unavailable", None, fmt)
        r += 1
    # EGI result: supplied, derived from the rent detail, or unavailable
    m = uw.get("egi", {})
    ws.cell(row=r, column=1, value="EGI (annual) — result").font = BOLD
    _state_cell(ws, r, m.get("value"), m.get("status"), m.get("reason"), MONEY_FMT)
    r += 1
    for key in ("operating_expenses", "occupancy"):
        label, fmt = INPUT_LABELS[key]
        ws.cell(row=r, column=1, value=label).font = BOLD
        v = inputs.get(key)
        _state_cell(ws, r, _display_value(key, v),
                    "supplied" if v is not None else "unavailable", None, fmt)
        r += 1

    # ---- Financing ----
    ws = wb.create_sheet("Financing")
    _header(ws, "Financing")
    r = 3
    for key in ("ltv", "interest_rate", "amortization_years"):
        label, fmt = INPUT_LABELS[key]
        ws.cell(row=r, column=1, value=label).font = BOLD
        v = inputs.get(key)
        _state_cell(ws, r, _display_value(key, v),
                    "supplied" if v is not None else "unavailable", None, fmt)
        r += 1
    for key in ("loan_amount", "initial_equity", "debt_service"):
        label, fmt = METRIC_LABELS[key]
        m = uw.get(key, {})
        ws.cell(row=r, column=1, value=label).font = BOLD
        _state_cell(ws, r, m.get("value"), m.get("status"), m.get("reason"), fmt)
        r += 1

    # ---- Returns ----
    ws = wb.create_sheet("Returns")
    _header(ws, "Returns")
    r = 3
    for key in ("cap_rate", "dscr", "cash_flow", "cash_on_cash",
                "exit_value", "irr", "equity_multiple"):
        label, fmt = METRIC_LABELS[key]
        m = uw.get(key, {})
        ws.cell(row=r, column=1, value=label).font = BOLD
        _state_cell(ws, r, m.get("value"), m.get("status"), m.get("reason"), fmt)
        r += 1

    # ---- Assumptions / Missing Data ----
    ws = wb.create_sheet("Assumptions - Missing Data")
    ws["A1"] = "Assumptions / Missing Data — what was supplied, calculated, " \
               "unavailable, or unknown"
    ws["A1"].font = TITLE
    ws["A2"] = "Item"
    ws["B2"] = "State"
    ws["C2"] = "Value"
    ws["D2"] = "Basis (inputs used)"
    for col in range(1, 5):
        c = ws.cell(row=2, column=col)
        c.fill = HDR_FILL
        c.font = HDR_FONT
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 52
    r = 3
    for key in ("purchase_price", "noi", "egi", "scheduled_rent_annual",
                "vacancy_loss_annual", "other_income_annual",
                "operating_expenses", "occupancy", "ltv", "interest_rate",
                "amortization_years", "hold_period", "exit_cap_rate",
                "closing_costs"):
        label = INPUT_LABELS[key][0]
        v = inputs.get(key)
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value="supplied" if v is not None else "unavailable")
        ws.cell(row=r, column=3,
                value=_display_value(key, v) if v is not None else "—")
        ws.cell(row=r, column=4, value="submitted by user" if v is not None else "")
        r += 1
    for key, (label, _fmt) in METRIC_LABELS.items():
        if key in ("noi", "egi"):
            continue  # covered below with their real result states
        m = uw.get(key, {})
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=m.get("status"))
        ws.cell(row=r, column=3,
                value=m.get("value") if m.get("value") is not None else "—")
        basis = ", ".join(m.get("inputs") or [])
        note = m.get("reason") or ""
        ws.cell(row=r, column=4, value="; ".join(x for x in (basis, note) if x))
        r += 1
    # NOI metric row (its state may be supplied/calculated/unavailable)
    m = uw.get("noi", {})
    ws.cell(row=r, column=1, value="NOI — result")
    ws.cell(row=r, column=2, value=m.get("status"))
    ws.cell(row=r, column=3, value=m.get("value") if m.get("value") is not None else "—")
    ws.cell(row=r, column=4,
            value="; ".join(x for x in (", ".join(m.get("inputs") or []),
                                       m.get("reason") or "") if x))
    r += 1
    # EGI metric row (its state may be supplied/calculated/unavailable)
    m = uw.get("egi", {})
    ws.cell(row=r, column=1, value="EGI — result")
    ws.cell(row=r, column=2, value=m.get("status"))
    ws.cell(row=r, column=3, value=m.get("value") if m.get("value") is not None else "—")
    ws.cell(row=r, column=4,
            value="; ".join(x for x in (", ".join(m.get("inputs") or []),
                                       m.get("reason") or "") if x))
    r += 1

    # ---- Due Diligence ----
    ws = wb.create_sheet("Due Diligence")
    ws["A1"] = "Due Diligence — %s" % deal["deal_id"]
    ws["A1"].font = TITLE
    ws["A2"] = "Category"
    ws["B2"] = "Item"
    ws["C2"] = "Status"
    ws["D2"] = "Notes"
    for col in range(1, 5):
        c = ws.cell(row=2, column=col)
        c.fill = HDR_FILL
        c.font = HDR_FONT
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 52
    r = 3
    for item in deal.get("dd_items") or []:
        ws.cell(row=r, column=1, value=item.get("category"))
        ws.cell(row=r, column=2, value=item.get("label")).font = BOLD
        ws.cell(row=r, column=3, value=item.get("status"))
        ws.cell(row=r, column=4, value=item.get("notes") or "")
        r += 1

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = True
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row,
                                   max_col=4):
            for c in row:
                c.alignment = Alignment(vertical="center", wrap_text=True)

    wb.save(path)
    return path
