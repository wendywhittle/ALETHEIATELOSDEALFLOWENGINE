"""Scenario analysis: base / bull / bear / custom deal re-underwrites.

A scenario is a named set of input overrides applied to one deal. Each
scenario is re-underwritten from scratch with underwriting.py (the same
deterministic math as the base deal), so scenarios never inherit stale
metrics. Ranges are widened to [lo, hi] and re-underwritten at both
endpoints; the base deal is never modified.

Units follow the engine: percentage-point fields (ltv, interest_rate,
exit_cap_rate) shift in points, so a 50-basis-point move is 0.5.

Pure analysis only. Persistence lives in records.py (SQLite + PostgreSQL).
"""

from __future__ import annotations

from typing import Any

from analytics_common import underwrite_point

RATE_BPS_SHIFT = 0.5  # 50 basis points, in the engine's percentage-point units


def as_range(value: Any) -> tuple[float, float]:
    """Normalize a scalar or [lo, hi] pair to a (lo, hi) tuple.

    Raises ValueError for malformed ranges (wrong length, non-numeric, or
    lo > hi). Single values become (value, value).
    """
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(f"Range must have exactly 2 elements, got {value!r}.")
        lo, hi = value
        if not all(isinstance(v, (int, float)) for v in (lo, hi)):
            raise ValueError(f"Range endpoints must be numeric, got {value!r}.")
        if lo > hi:
            raise ValueError(f"Range lower bound {lo} exceeds upper bound {hi}.")
        return (float(lo), float(hi))
    if isinstance(value, (int, float)):
        return (float(value), float(value))
    raise ValueError(f"Cannot interpret {value!r} as a number or [lo, hi] range.")


def underwrite_range(inputs: dict[str, Any]) -> dict[str, Any]:
    """Underwrite a deal whose inputs may be [lo, hi] ranges.

    Returns {"lo": {...}, "hi": {...}} with the derived-metrics dict of each
    endpoint (each a full re-underwrite via underwriting.py), plus
    "point_estimate" when every input is scalar (lo == hi everywhere).
    """
    lo_inputs: dict[str, Any] = {}
    hi_inputs: dict[str, Any] = {}
    has_range = False
    for key, value in inputs.items():
        if value is None:
            # Missing stays missing: never invented, never zero-filled.
            lo_inputs[key] = None
            hi_inputs[key] = None
            continue
        lo, hi = as_range(value)
        lo_inputs[key] = lo
        hi_inputs[key] = hi
        if lo != hi:
            has_range = True
    result = {
        "lo": underwrite_point(lo_inputs)["derived"],
        "hi": underwrite_point(hi_inputs)["derived"],
    }
    if not has_range:
        result["point_estimate"] = result["lo"]
    return result


def _effective_noi(inputs: dict[str, Any]) -> float | None:
    """Resolve the point NOI used by the default bull/bear perturbations."""
    base, _ = as_range(inputs.get("noi")) if "noi" in inputs else (None, None)
    if isinstance(base, (int, float)):
        return float(base)
    lo, hi = (None, None)
    try:
        lo, hi = as_range(inputs.get("noi"))
    except (ValueError, TypeError):
        return None
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    return None


def default_scenarios(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the three built-in scenarios for a deal's inputs.

    - base: inputs unchanged.
    - bull: NOI +10%, exit cap rate -50bps, interest rate -50bps, hold
      one year longer.
    - bear: NOI -15%, exit cap rate +100bps, interest rate +100bps, hold
      one year shorter (minimum 1 year).

    Rate floors: exit cap rate never below 0.5%, interest rate never below
    0.1%. Perturbations apply to ranged inputs endpoint-wise; scalar NOI
    overrides take precedence. Notes describe exactly what each override
    does, so a result can be traced to its perturbation.
    """
    scenarios = [
        {
            "name": "base",
            "description": "Inputs as supplied; no perturbations.",
            "overrides": {},
        }
    ]
    shifts = [
        (
            "bull",
            "NOI +10%, exit cap -50bps, rate -50bps, hold +1yr.",
            {"noi": 1.10, "exit_cap_rate": -RATE_BPS_SHIFT, "interest_rate": -RATE_BPS_SHIFT, "hold_period": 1},
        ),
        (
            "bear",
            "NOI -15%, exit cap +100bps, rate +100bps, hold -1yr (min 1).",
            {"noi": 0.85, "exit_cap_rate": 2 * RATE_BPS_SHIFT, "interest_rate": 2 * RATE_BPS_SHIFT, "hold_period": -1},
        ),
    ]
    for name, description, shift in shifts:
        overrides: dict[str, Any] = {}
        notes: list[str] = []
        noi = _effective_noi(inputs)
        if noi is not None:
            overrides["noi"] = noi * shift["noi"]
            pct = (shift["noi"] - 1) * 100
            notes.append(f"noi {pct:+.0f}% (point estimate of ranged input)" if isinstance(inputs.get("noi"), (list, tuple)) else f"noi {pct:+.0f}%")
        for key, floor in (("exit_cap_rate", 0.5), ("interest_rate", 0.1)):
            if key in inputs and isinstance(inputs[key], (int, float)):
                new_value = max(floor, inputs[key] + shift[key])
                overrides[key] = new_value
                bps = (new_value - inputs[key]) * 100
                notes.append(f"{key} {bps:+.0f}bps (floor {floor}%)")
        if "hold_period" in inputs and isinstance(inputs["hold_period"], (int, float)):
            overrides["hold_period"] = max(1, inputs["hold_period"] + shift["hold_period"])
            notes.append(f"hold_period {shift['hold_period']:+.0f}yr (min 1yr)")
        scenarios.append(
            {"name": name, "description": description, "overrides": overrides, "notes": notes}
        )
    return scenarios


def run_scenarios(
    record: dict[str, Any],
    scenarios: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run scenarios against a deal record; returns per-scenario results.

    ``record`` is the engine's canonical deal dict (``inputs`` plus
    ``underwriting``). Custom scenarios are dicts with "name" and
    "overrides"; unknown keys or malformed ranges raise ValueError. Each
    scenario is re-underwritten from scratch, so results carry their own
    ``underwrite`` and ``missing`` rather than inheriting the base deal's.
    """
    inputs = record.get("inputs") or {}
    wanted = scenarios if scenarios is not None else default_scenarios(inputs)
    results: list[dict[str, Any]] = []
    for spec in wanted:
        name = spec.get("name") or "custom"
        overrides = dict(spec.get("overrides") or {})
        try:
            merged = {**inputs, **overrides}
            uw = underwrite_range(merged)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Scenario {name!r} has invalid overrides: {exc}")
        entry: dict[str, Any] = {
            "name": name,
            "description": spec.get("description") or "",
            "overrides": overrides,
        }
        if "notes" in spec:
            entry["notes"] = spec["notes"]
        if "point_estimate" in uw:
            # All-scalar inputs: one full re-underwrite, missing fields named.
            entry["underwrite"] = uw["point_estimate"]
            entry["missing"] = underwrite_point(merged)["missing"]
        else:
            # Ranged inputs: both endpoints re-underwritten; missing is the
            # union of the two endpoint input dicts' missing fields.
            entry["underwrite"] = uw["lo"]
            entry["range"] = {"lo": uw["lo"], "hi": uw["hi"]}
            missing: set[str] = set()
            for endpoint in ("lo", "hi"):
                ep_inputs = {k: (v[0] if endpoint == "lo" else v[1]) if isinstance(v, (list, tuple)) else v
                             for k, v in merged.items()}
                missing |= set(underwrite_point(ep_inputs)["missing"])
            entry["missing"] = sorted(missing)
        results.append(entry)
    return results
