"""Per-deal evidence log — typed claims (Charter evidence-before-assertion).

Every material claim attached to a deal is recorded with its epistemic
type, its source, and a timestamp. The type ladder is fixed so downstream
analysis (perspectives, observer) can weigh evidence honestly instead of
treating all claims alike.

Pure validation helpers only. Persistence lives in records.py so the tables
work on both SQLite and PostgreSQL.
"""

from __future__ import annotations

EVIDENCE_TYPES = ("observed", "sourced", "calculated", "assumed", "hypothesis")


def validate_evidence(evidence_type: str, content: str) -> None:
    """Raise ValueError for a bad type or empty content."""
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(
            f"Invalid evidence type {evidence_type!r}; must be one of {EVIDENCE_TYPES}."
        )
    if not content or not str(content).strip():
        raise ValueError("Evidence content must not be empty.")
