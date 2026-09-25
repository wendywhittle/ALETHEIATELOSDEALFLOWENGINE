"""Shared helpers for the charter-analytics modules.

The analytics (perspectives, scenarios, simulation) were designed against a
``{"derived": {metric: scalar}, "missing": [...]}`` underwriting shape. This
module adapts the target engine's ``underwriting.calculate()`` — which
returns ``{metric: {"value", "status", "inputs", "reason"}}`` with Wendy's
missing-stays-missing rules — into that shape. Zero duplicated formulas:
every number comes from underwriting.py.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import underwriting
import validation


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def underwrite_point(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run the target underwriting and return the analytics shape.

    Returns ``{"derived": {metric: scalar}, "missing": [input fields]}``.

    - ``derived`` holds only finite scalars for computable metrics. Two
      alias keys the lenses expect are added: ``annual_cash_flow``
      (from ``cash_flow``) and ``annual_debt_service`` (from
      ``debt_service``). ``noi_basis`` records whether NOI was supplied or
      derived, for the Skeptic lens.
    - ``missing`` lists input fields that are None. Missing stays missing:
      nothing is invented or zero-filled here.
    """
    result = underwriting.calculate(inputs)
    derived: dict[str, Any] = {}
    for key, metric in result.items():
        value = metric["value"]
        if _is_number(value):
            derived[key] = value
    if "cash_flow" in derived:
        derived["annual_cash_flow"] = derived["cash_flow"]
    if "debt_service" in derived:
        derived["annual_debt_service"] = derived["debt_service"]
    noi_status = result.get("noi", {}).get("status")
    if noi_status == "supplied":
        derived["noi_basis"] = "supplied"
    elif noi_status == "calculated":
        derived["noi_basis"] = "derived (EGI - operating expenses)"
    missing = [f for f in validation.INPUT_FIELDS if inputs.get(f) is None]
    return {"derived": derived, "missing": missing}
