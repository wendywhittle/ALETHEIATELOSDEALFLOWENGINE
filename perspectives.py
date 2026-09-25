"""Competing perspectives on a deal.

Four deterministic, documented analytical lenses: Bull, Bear (contrarian),
Quant, and Skeptic.

These are NOT autonomous agents and they do not simulate AI disagreement.
Each lens is a fixed, auditable methodology: a pure function of the deal's
own inputs, derived metrics, missing data, and evidence log. The methodology
for each lens is stated in its docstring so any output can be traced back to
the rule that produced it.

Dissent is preserved structurally: each lens is stored as a separate record
and lenses are never merged, averaged, or synthesized into a single view.
Disagreement stays visible.

Pure analysis only. Persistence lives in records.py (SQLite + PostgreSQL).
The underwriting numbers come from underwriting.py via analytics_common —
no duplicated formulas.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from analytics_common import underwrite_point

LENSES = ("Bull", "Bear", "Quant", "Skeptic")

_ANALYSIS_KEYS = (
    "belief",
    "why",
    "supporting_evidence",
    "contradicting_evidence",
    "key_assumptions",
    "uncertainty",
    "what_would_change_conclusion",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _money(value: Any) -> str:
    return "unknown" if not _num(value) else f"${value:,.0f}"


def _pct(value: Any) -> str:
    return "unknown" if not _num(value) else f"{value * 100:.2f}%"


def _ctx(record: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the shared fact base every lens reads from.

    ``record`` is the target engine's canonical deal dict (``inputs`` plus
    the ``underwriting`` metric results). Derived scalars and the missing
    list are computed from underwriting.py — never invented.
    """
    import json  # local import keeps module import-light

    inputs = dict(record.get("inputs") or {})
    uw = underwrite_point(inputs)
    derived = uw["derived"]
    missing = uw["missing"]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_items:
        by_type.setdefault(item.get("type", "unknown"), []).append(item)
    return {
        "inputs": inputs,
        "derived": derived,
        "missing": missing,
        "evidence": evidence_items,
        "by_type": by_type,
        "json": json,
    }


def _supplied_inputs(inputs: dict[str, Any]) -> list[str]:
    return sorted(k for k, v in inputs.items() if v is not None and not isinstance(v, (list, tuple)))


def _ranged_inputs(inputs: dict[str, Any]) -> list[str]:
    return sorted(
        k for k, v in inputs.items()
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)
    )


# ---------------------------------------------------------------------------
# Lens methodologies
# ---------------------------------------------------------------------------

def _bull(ctx: dict[str, Any]) -> dict[str, Any]:
    """Bull lens methodology (documented, deterministic).

    1. Take every computable derived metric at face value, as supplied.
    2. State the strongest factual case the numbers support.
    3. Supporting side: derived metrics plus evidence typed observed, sourced,
       or calculated.
    4. Contradicting side (required for honesty): every missing field plus
       evidence typed assumed or hypothesis. The bull case must name what
       could kill it.
    5. Assumptions are stated explicitly, never smuggled in.
    6. Falsifiers are concrete thresholds, not vibes.
    """
    d, missing, by_type = ctx["derived"], ctx["missing"], ctx["by_type"]
    why: list[str] = []
    if _num(d.get("cap_rate")):
        why.append(f"Cap rate {_pct(d['cap_rate'])} on stated NOI of {_money(d.get('noi') or ctx['inputs'].get('noi'))}.")
    if _num(d.get("dscr")):
        why.append(f"DSCR {d['dscr']:.2f}x: NOI covers debt service {d['dscr']:.1f} times over.")
    if _num(d.get("cash_on_cash")):
        why.append(f"Cash-on-cash {_pct(d['cash_on_cash'])} on {_money(d.get('initial_equity'))} of initial equity.")
    if _num(d.get("irr")):
        why.append(f"Modeled IRR {_pct(d['irr'])} over the hold period.")
    if _num(d.get("annual_cash_flow")) and d["annual_cash_flow"] > 0:
        why.append(f"Positive annual cash flow of {_money(d['annual_cash_flow'])} after debt service.")
    if not why:
        why.append("No derived return metrics are computable from the supplied inputs; the bull case is unproven until data exists.")

    supporting = [
        f"[{e['type']}] {e['content']}" + (f" (source: {e['source']})" if e.get("source") else "")
        for t in ("observed", "sourced", "calculated")
        for e in by_type.get(t, [])
    ]
    contradicting = [f"Missing input: {m}" for m in missing] + [
        f"[{e['type']}] {e['content']}" for t in ("assumed", "hypothesis") for e in by_type.get(t, [])
    ]
    return {
        "belief": "Worth pursuing if the stated numbers hold.",
        "why": why,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "key_assumptions": [
            "Supplied NOI is accurate and sustainable, not a broker-inflated trailing figure.",
            "Financing terms (rate, LTV, amortization) are obtainable as modeled.",
            "Exit cap rate is achievable in the future market.",
            "No undisclosed capital expenditures, deferred maintenance, or liabilities.",
        ],
        "uncertainty": [f"{m} is unknown; the bull case assumes it resolves favorably." for m in missing]
        or ["No missing inputs; uncertainty is limited to forecast error."],
        "what_would_change_conclusion": [
            "NOI verified 15%+ below the supplied figure.",
            "Exit cap rate 100bps+ above the modeled rate.",
            "Interest rate 100bps+ above the modeled rate.",
            "DSCR falling below 1.0x under any plausible stress.",
        ],
    }


def _bear(ctx: dict[str, Any]) -> dict[str, Any]:
    """Bear (contrarian) lens methodology (documented, deterministic).

    1. Invert the Bull: assume inputs disappoint and missing data hides risk.
    2. Compute fragility arithmetically: break-even NOI (the NOI at which
       DSCR = 1.0, i.e. annual debt service) and the NOI cushion percentage.
    3. Supporting side: missing fields, assumed/hypothesis evidence, and the
       fragility math.
    4. Contradicting side (required steelman): the strongest derived metrics,
       so the bear case cannot pretend they do not exist.
    5. Falsifiers of the bear case are verifications, not hopes.
    """
    d, missing, by_type = ctx["derived"], ctx["missing"], ctx["by_type"]
    inputs = ctx["inputs"]
    why: list[str] = []
    noi = d.get("noi") if _num(d.get("noi")) else (inputs.get("noi") if _num(inputs.get("noi")) else None)
    debt = d.get("annual_debt_service") if _num(d.get("annual_debt_service")) else None
    if _num(noi) and _num(debt) and noi > 0:
        cushion = (noi - debt) / noi
        why.append(
            f"Break-even NOI is {_money(debt)} (DSCR = 1.0x); stated NOI of {_money(noi)} "
            f"can fall {cushion * 100:.0f}% before debt service is uncovered."
        )
        if cushion < 0.2:
            why.append("Cushion under 20%: a mild vacancy or expense surprise wipes out coverage.")
    elif _num(noi):
        why.append("No debt modeled, so downside is driven by purchase price versus achievable NOI.")
    else:
        why.append("NOI itself is unavailable: the downside cannot even be quantified.")
    if missing:
        why.append(f"{len(missing)} inputs missing ({', '.join(missing)}); each is a place where bad news can hide.")
    n_soft = len(by_type.get("assumed", [])) + len(by_type.get("hypothesis", []))
    if n_soft:
        why.append(f"{n_soft} evidence item(s) are assumptions or hypotheses, not facts.")
    if not why:
        why.append("Inputs are complete and coverage is strong; the bear case rests on market-level risks.")

    supporting = (
        [f"Missing input: {m}" for m in missing]
        + [f"[{e['type']}] {e['content']}" for t in ("assumed", "hypothesis") for e in by_type.get(t, [])]
    )
    contradicting = [
        f"[{e['type']}] {e['content']}" + (f" (source: {e['source']})" if e.get("source") else "")
        for t in ("observed", "sourced", "calculated")
        for e in by_type.get(t, [])
    ]
    if _num(d.get("dscr")) and d["dscr"] >= 1.25:
        contradicting.append(f"DSCR {d['dscr']:.2f}x meets a conventional 1.25x coverage bar.")
    if _num(d.get("cap_rate")):
        contradicting.append(f"Cap rate {_pct(d['cap_rate'])} is a factual return on stated numbers.")
    return {
        "belief": "The deal fails if any key input disappoints; price paid is the risk.",
        "why": why,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "key_assumptions": [
            "Stated NOI may be overstated; verify against leases and bank statements.",
            "Missing inputs, once known, could be worse than neutral.",
            "Exit conditions in the future market may be worse than modeled.",
        ],
        "uncertainty": [f"{m} is unknown; the bear case assumes it resolves adversely." for m in missing]
        or ["Inputs are complete; remaining uncertainty is market-level."],
        "what_would_change_conclusion": [
            "NOI independently verified at or above the stated figure.",
            "Interest rate locked at or below the modeled rate.",
            "Break-even cushion above 30% with verified inputs.",
        ],
    }


def _sensitivity(inputs: dict[str, Any], derived: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic ±10% finite-difference sensitivity of IRR (else cap rate).

    Perturbs each numeric scalar input independently; ranged inputs are
    skipped and reported as skipped. Returns the top 3 drivers by absolute
    metric movement. Every trial runs through underwriting.py.
    """
    base_metric = derived.get("irr") if _num(derived.get("irr")) else derived.get("cap_rate")
    metric_name = "irr" if _num(derived.get("irr")) else "cap_rate"
    if not _num(base_metric):
        return []
    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    for key, value in inputs.items():
        if isinstance(value, (list, tuple)):
            skipped.append(key)
            continue
        if not _num(value) or value == 0:
            continue
        for direction, factor in (("down", 0.9), ("up", 1.1)):
            trial = dict(inputs)
            trial[key] = value * factor
            try:
                out = underwrite_point(trial)["derived"].get(metric_name)
            except Exception:
                out = None
            if _num(out):
                results.append({"input": key, "direction": direction, "delta": out - base_metric})
    by_input: dict[str, float] = {}
    for r in results:
        by_input[r["input"]] = max(by_input.get(r["input"], 0.0), abs(r["delta"]))
    ranked = sorted(by_input.items(), key=lambda kv: kv[1], reverse=True)[:3]
    drivers = [{"input": k, "max_abs_delta": v, "metric": metric_name} for k, v in ranked]
    if skipped:
        drivers.append({"input": f"skipped (ranged): {', '.join(sorted(skipped))}", "max_abs_delta": None, "metric": metric_name})
    return drivers


def _quant(ctx: dict[str, Any]) -> dict[str, Any]:
    """Quant lens methodology (documented, deterministic).

    1. Numbers only: report what is computable, name what is not.
    2. Run ±10% finite-difference sensitivity on each numeric input against
       IRR (falling back to cap rate); rank the top 3 drivers.
    3. No qualitative opinion is offered; where the math is incomplete the
       lens says so explicitly.
    """
    d, missing = ctx["derived"], ctx["missing"]
    inputs = ctx["inputs"]
    drivers = _sensitivity(inputs, d)
    computable = sorted(k for k, v in d.items() if _num(v) and not k.endswith("_basis"))
    if computable:
        belief = f"The math is computable for: {', '.join(computable)}."
    else:
        belief = "The math is incomplete: no return metric is computable from the inputs."
    why = [f"{k} = {d[k]:.4f}" for k in computable] or ["No numeric derived metrics available."]
    if drivers:
        real = [x for x in drivers if x["max_abs_delta"] is not None]
        if real:
            why.append(
                "Top sensitivity drivers (±10% input move): "
                + "; ".join(f"{x['input']} (Δ{x['metric']} {x['max_abs_delta']:+.4f})" for x in real)
                + "."
            )
    return {
        "belief": belief,
        "why": why,
        "supporting_evidence": [f"Derived: {k} = {d[k]:.4f}" for k in computable],
        "contradicting_evidence": [f"Missing input: {m}" for m in missing],
        "key_assumptions": [
            "Inputs are accurate as supplied.",
            "The underwriting model is structurally correct for this deal.",
            "Sensitivity is local (±10%) and does not capture interactions.",
        ],
        "uncertainty": [f"{m} unknown; metrics depending on it are absent, not zero." for m in missing]
        or ["All modeled inputs present; uncertainty is input accuracy, not completeness."],
        "what_would_change_conclusion": [
            "Any revision to a top-3 sensitivity driver input.",
            "Filling a missing input that unlocks a currently absent metric.",
        ],
    }


def _skeptic(ctx: dict[str, Any]) -> dict[str, Any]:
    """Skeptic lens methodology (documented, deterministic).

    1. Audit provenance: classify every input as supplied, ranged (uncertain),
       or missing; note whether NOI was supplied or derived.
    2. Audit the evidence log by type: sourced/observed/calculated versus
       assumed/hypothesis. Quote soft items so their softness is visible.
    3. The Skeptic judges the analysis, not the deal: its output is a list
       of reasons the current numbers might not mean what they seem to mean.
    """
    inputs, d, missing, by_type = ctx["inputs"], ctx["derived"], ctx["missing"], ctx["by_type"]
    supplied = _supplied_inputs(inputs)
    ranged = _ranged_inputs(inputs)
    why = [
        f"Provenance: {len(supplied)} supplied, {len(ranged)} ranged, {len(missing)} missing "
        f"out of {len(supplied) + len(ranged) + len(missing)} modeled inputs."
    ]
    hard = sum(len(by_type.get(t, [])) for t in ("observed", "sourced", "calculated"))
    soft_items = [e for t in ("assumed", "hypothesis") for e in by_type.get(t, [])]
    why.append(f"Evidence log: {hard} hard item(s), {len(soft_items)} soft (assumed/hypothesis).")
    if "noi_basis" in d:
        why.append(f"NOI basis is '{d['noi_basis']}'; treat derived metrics accordingly.")
    for e in soft_items[:5]:
        why.append(f"Soft claim in the record: [{e['type']}] {e['content']}")
    if not soft_items and not missing:
        why.append("No soft claims and no missing inputs found in this record; skepticism moves to market-level doubts.")
    return {
        "belief": "The analysis is only as good as its weakest input.",
        "why": why,
        "supporting_evidence": [
            f"[{e['type']}] {e['content']}" for t in ("observed", "sourced", "calculated") for e in by_type.get(t, [])
        ],
        "contradicting_evidence": [f"[{e['type']}] {e['content']}" for e in soft_items]
        + [f"Missing input: {m}" for m in missing],
        "key_assumptions": [
            "That the supplied numbers were measured, not wished for.",
            "That the evidence log is complete (absence of a soft claim is not proof of hardness).",
        ],
        "uncertainty": [f"{m}: unknown and unmodeled." for m in missing]
        + ([f"{r}: given as a range, point metrics overstate precision." for r in ranged] or []),
        "what_would_change_conclusion": [
            "Independent verification of NOI (leases, bank statements).",
            "Replacing each assumed/hypothesis evidence item with sourced evidence.",
            "Filling every missing input with a measured value.",
        ],
    }


_LENS_FNS = {"Bull": _bull, "Bear": _bear, "Quant": _quant, "Skeptic": _skeptic}


def analyze_deal(
    record: dict[str, Any],
    evidence_items: list[dict[str, Any]] | None = None,
    lenses: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the selected lenses (default: all four) over one deal record.

    ``record`` is the engine's canonical deal dict (``inputs`` plus
    ``underwriting``). Pure function; nothing is persisted here.
    """
    wanted = list(lenses) if lenses else list(LENSES)
    for lens in wanted:
        if lens not in LENSES:
            raise ValueError(f"Unknown lens {lens!r}; must be one of {LENSES}.")
    ctx = _ctx(record, evidence_items or [])
    out: dict[str, dict[str, Any]] = {}
    for lens in wanted:
        analysis = _LENS_FNS[lens](ctx)
        missing_keys = [k for k in _ANALYSIS_KEYS if k not in analysis]
        if missing_keys:
            raise RuntimeError(f"Lens {lens} failed to produce keys: {missing_keys}")
        out[lens] = {"lens": lens, **analysis}
    return out
