"""Seeded Monte Carlo risk simulation over one deal's inputs.

Draws n independent samples from uniform ranges (or point values) over the
deal's inputs, re-underwrites each trial with underwriting.py, and reports
distributional summaries (mean, stdev, p5/p25/p50/p75/p95) plus the share of
trials hitting downside guardrails (DSCR < 1.0, cash-on-cash < 0, IRR < 0).

Statistical honesty is enforced and documented in the summary:

- Draws are INDEPENDENT across inputs. Real estate inputs are correlated
  (cap rates move with rates; NOI with occupancy); those correlations are
  NOT modeled. The summary says so explicitly.
- Uniform ranges are used; the summary names the draw distribution.
- Trials whose underwriting yields nothing computable are counted as
  failed trials, not dropped silently.
- The seed is stored with the result so a run is exactly reproducible.
- Missing inputs stay missing: an input not in the ranges dict is held at
  its point value, never invented.

Pure simulation only. Persistence lives in records.py (SQLite + PostgreSQL).
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any

from analytics_common import underwrite_point

# Inputs eligible for range sampling, in the engine's field names/units.
# Percentage-point fields (ltv, interest_rate, exit_cap_rate) take ranges in
# points; occupancy is a 0-100 percentage.
KNOWN_INPUTS = (
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
)

# Metrics summarized across trials.
SUMMARY_METRICS = (
    "cap_rate",
    "dscr",
    "cash_on_cash",
    "irr",
    "cash_flow",
    "total_profit",
    "total_return_multiple",
)

DEFAULT_TRIALS = 10_000
MAX_TRIALS = 100_000

_LIMITATIONS = (
    "Draws are independent across inputs; real-world correlations "
    "(cap rates with interest rates, NOI with occupancy) are NOT modeled, "
    "so joint tail risk is understated.",
    "Inputs are drawn from uniform ranges (or held at point values); "
    "this is not a fitted probability model.",
    "Each trial re-underwrites the deal deterministically; trials that "
    "yield no computable metric are counted as failed, not dropped silently.",
)


def _as_range(value: Any) -> tuple[float, float]:
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


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        raise ValueError("Cannot take a percentile of an empty sample.")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo_idx = int(math.floor(rank))
    hi_idx = int(math.ceil(rank))
    if lo_idx == hi_idx:
        return sorted_vals[lo_idx]
    frac = rank - lo_idx
    return sorted_vals[lo_idx] * (1 - frac) + sorted_vals[hi_idx] * frac


def run_simulation(
    record: dict[str, Any],
    ranges: dict[str, Any] | None = None,
    n: int = DEFAULT_TRIALS,
    seed: int = 42,
) -> dict[str, Any]:
    """Run n seeded trials and return the distributional summary.

    ``record`` is the engine's canonical deal dict (``inputs`` plus
    ``underwriting``). ``ranges`` maps input names to point values or
    [lo, hi] pairs; unknown inputs or malformed ranges raise ValueError.
    Inputs absent from ``ranges`` are held at the deal's point values.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}.")
    if n > MAX_TRIALS:
        raise ValueError(f"n must be <= {MAX_TRIALS}, got {n}.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"seed must be an integer, got {seed!r}.")

    base_inputs = dict(record.get("inputs") or {})
    ranges = dict(ranges or {})
    for key in ranges:
        if key not in KNOWN_INPUTS:
            raise ValueError(f"Unknown input {key!r}; must be one of {KNOWN_INPUTS}.")

    sampled: dict[str, tuple[float, float]] = {}
    for key in KNOWN_INPUTS:
        if key in ranges:
            sampled[key] = _as_range(ranges[key])
        elif isinstance(base_inputs.get(key), (int, float)):
            sampled[key] = _as_range(base_inputs[key])
        else:
            sampled[key] = (None, None)  # type: ignore[assignment]

    rng = random.Random(seed)
    metric_samples: dict[str, list[float]] = {m: [] for m in SUMMARY_METRICS}
    guardrail_hits = {"dscr_below_1": 0, "cash_on_cash_negative": 0, "irr_negative": 0}
    trials_with_guardrails = 0
    failed_trials = 0

    for _ in range(n):
        trial: dict[str, Any] = {}
        for key, (lo, hi) in sampled.items():
            if lo is None:
                trial[key] = None
            elif lo == hi:
                trial[key] = lo
            else:
                trial[key] = rng.uniform(lo, hi)
        derived = underwrite_point(trial)["derived"]
        if not any(m in derived for m in SUMMARY_METRICS):
            failed_trials += 1
            continue
        for metric in SUMMARY_METRICS:
            value = derived.get(metric)
            if isinstance(value, (int, float)) and math.isfinite(value):
                metric_samples[metric].append(float(value))
        trials_with_guardrails += 1
        dscr = derived.get("dscr")
        coc = derived.get("cash_on_cash")
        irr = derived.get("irr")
        if isinstance(dscr, (int, float)) and math.isfinite(dscr) and dscr < 1.0:
            guardrail_hits["dscr_below_1"] += 1
        if isinstance(coc, (int, float)) and math.isfinite(coc) and coc < 0:
            guardrail_hits["cash_on_cash_negative"] += 1
        if isinstance(irr, (int, float)) and math.isfinite(irr) and irr < 0:
            guardrail_hits["irr_negative"] += 1

    summary: dict[str, Any] = {
        "metric_summaries": {},
        "guardrail_probabilities": {},
        "n_trials": n,
        "failed_trials": failed_trials,
        "seed": seed,
        "draw_distribution": "independent uniform over each input's range",
        "limitations": list(_LIMITATIONS),
    }
    for metric, vals in metric_samples.items():
        if not vals:
            summary["metric_summaries"][metric] = {"n": 0, "note": "not computable in any trial"}
            continue
        vals.sort()
        summary["metric_summaries"][metric] = {
            "n": len(vals),
            "mean": statistics.fmean(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "p5": _percentile(vals, 5),
            "p25": _percentile(vals, 25),
            "p50": _percentile(vals, 50),
            "p75": _percentile(vals, 75),
            "p95": _percentile(vals, 95),
            "min": vals[0],
            "max": vals[-1],
        }
    denom = trials_with_guardrails if trials_with_guardrails else 1
    summary["guardrail_probabilities"] = {
        key: hits / denom for key, hits in guardrail_hits.items()
    }
    summary["guardrail_denominator"] = trials_with_guardrails
    return summary
