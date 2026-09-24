"""Production entry point: `gunicorn wsgi:app`."""

from app import app  # noqa: F401

import seed

# The free-tier database is ephemeral: every restart wipes it. Re-seed the
# curated pipeline on every boot so the deals (and the home-run triage)
# restore themselves. Idempotent - existing deals are never duplicated.
seed.seed_database()
