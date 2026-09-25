"""Decision journal: thesis records, outcome records, and the observer view.

Governance rules, stated plainly because they are load-bearing:

- A thesis may only be created while its deal is in PURSUE status. Theses
  record a falsifiable belief formed BEFORE the outcome is known; writing
  them earlier turns the journal into post-hoc storytelling.
- Theses are immutable: there is no edit path, only a later outcome record
  that agrees or disagrees with the thesis. The observer view compares
  outcomes against the theses they follow, in order.
- The observer is an audit view over the journal. It compares predictions
  to outcomes and reports hit rate; it does not judge deals.

Pure business logic only. Persistence lives in records.py (SQLite +
PostgreSQL).
"""

from __future__ import annotations

from typing import Any

# Only a deal whose pipeline status is PURSUE may carry a thesis.
THESIS_ALLOWED_STATUS = "PURSUE"


def check_thesis_allowed(deal: dict[str, Any] | None, deal_id: str) -> None:
    """Enforce the thesis rule: the deal must exist and be in PURSUE.

    Raises KeyError if the deal does not exist, ValueError if its status
    is anything other than PURSUE.
    """
    if deal is None:
        raise KeyError(f"Deal {deal_id} not found.")
    status = (deal.get("status") or "").upper()
    if status != THESIS_ALLOWED_STATUS:
        raise ValueError(
            f"Theses can only be recorded for deals in {THESIS_ALLOWED_STATUS} status; "
            f"deal {deal_id} is {status or 'UNKNOWN'}."
        )


def _rec_key(entry: dict[str, Any]) -> tuple:
    """Total recording order within one journal table."""
    return (entry["created_at"], entry["id"])


def build_observer_summary(
    theses: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    deals_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the observer audit view from journal rows and deal lookups.

    ``theses`` and ``outcomes`` are row dicts (each with at least ``id``,
    ``deal_id``, and ``created_at``). ``deals_by_id`` maps deal ids to
    ``{"name", "status"}``.

    Matching rule: theses are processed in recording order; each thesis is
    attached to the earliest outcome for the same deal recorded strictly
    AFTER the thesis (by ``created_at``). The two tables have independent
    id sequences, so ids are never compared across tables — only
    timestamps. An outcome that cannot be shown to come after any thesis
    is listed as unmatched rather than force-fitted.

    Returns a plain dict, safe to serialize.
    """
    per_deal: list[dict[str, Any]] = []
    matched_outcome_ids: set[int] = set()
    outcomes_by_deal: dict[str, list[dict[str, Any]]] = {}
    for outcome in outcomes:
        outcomes_by_deal.setdefault(outcome["deal_id"], []).append(outcome)
    for deal_outcomes in outcomes_by_deal.values():
        deal_outcomes.sort(key=_rec_key)

    for thesis in sorted(theses, key=_rec_key):
        deal_id = thesis["deal_id"]
        deal = deals_by_id.get(deal_id, {})
        followups = [
            o for o in outcomes_by_deal.get(deal_id, [])
            if o["created_at"] > thesis["created_at"]
            and o["id"] not in matched_outcome_ids
        ]
        matched = followups[0] if followups else None
        if matched is not None:
            matched_outcome_ids.add(matched["id"])
        per_deal.append(
            {
                "deal_id": deal_id,
                "deal_name": deal.get("name"),
                "deal_status": deal.get("status"),
                "thesis": thesis,
                "outcome": matched,
            }
        )

    unmatched = [
        o for o in outcomes
        if o["id"] not in matched_outcome_ids
        and not any(
            o["deal_id"] == t["deal_id"] and o["created_at"] > t["created_at"]
            for t in theses
        )
    ]

    resolved = [d for d in per_deal if d["outcome"] is not None]
    hit_rate = (
        sum(1 for d in resolved if (d["outcome"] or {}).get("thesis_agreement") == "agree") / len(resolved)
        if resolved
        else None
    )
    return {
        "theses": per_deal,
        "unmatched_outcomes": unmatched,
        "counts": {
            "theses": len(theses),
            "outcomes": len(outcomes),
            "resolved": len(resolved),
            "unresolved": len(theses) - len(resolved),
            "unmatched_outcomes": len(unmatched),
        },
        "hit_rate": hit_rate,
        "note": "Hit rate compares recorded outcomes against the theses they follow, in order. "
        "It measures the journal's predictive calibration, not deal quality.",
    }
