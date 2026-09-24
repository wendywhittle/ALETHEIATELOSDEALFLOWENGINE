"""Production entry point: `gunicorn wsgi:app`."""

from app import app  # noqa: F401
