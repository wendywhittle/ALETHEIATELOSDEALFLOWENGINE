"""PostgreSQL backend tests: dialect translation and backend selection.

These tests run without a live PostgreSQL server. They drive records.py
with DATABASE_URL set and a recording fake standing in for psycopg,
asserting that every statement executed on the Postgres path is valid
Postgres dialect (no `?` placeholders, no SQLite-isms) and that the
SQLite path is untouched when DATABASE_URL is absent.
"""

import datetime
import json
import os
import re
import unittest

import records


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 1

    def execute(self, sql, params=None):
        self._conn.statements.append(sql)
        return self

    def fetchone(self):
        if self._conn.results:
            return self._conn.results.pop(0)
        return None

    def fetchall(self):
        if self._conn.results:
            res = self._conn.results.pop(0)
            return res if isinstance(res, list) else []
        return []


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.results = []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        return FakeCursor(self).execute(sql, params)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class FakePsycopg:
    """Stands in for the psycopg module: records.psycopg.connect()."""

    def __init__(self):
        self.connections = []
        self.seen_urls = []
        self.seen_kwargs = []

    def connect(self, url, **kwargs):
        self.seen_urls.append(url)
        self.seen_kwargs.append(kwargs)
        conn = FakeConnection()
        self.connections.append(conn)
        return conn


FULL_ROW = {
    "deal_id": "AT-2026-000007",
    "name": "Sample Deal",
    "asset_type": "Commercial",
    "location": "Vancouver, WA",
    "source": "LoopNet",
    "inputs_json": json.dumps({"purchase_price": 10_000_000, "noi": 650_000}),
    "underwriting_json": json.dumps({"cap_rate": {"status": "calculated"}}),
    "contact_json": json.dumps({"contact_name": "Wendy"}),
    "status": "NEW",
    "artifact_path": None,
    "created_at": "2026-09-24T00:00:00+00:00",
    "updated_at": "2026-09-24T00:00:00+00:00",
}


class PostgresDialectCase(unittest.TestCase):
    def setUp(self):
        self._real_psycopg = records.psycopg
        self._real_dict_row = records.dict_row
        self._real_db_url = os.environ.get("DATABASE_URL")
        self.fake = FakePsycopg()
        records.psycopg = self.fake
        records.dict_row = object()
        os.environ["DATABASE_URL"] = \
            "postgresql://user:pass@localhost:5432/dealflow"
        # Make sure no explicit-path/SQLite env interferes.
        self._real_dealflow_db = os.environ.pop("DEALFLOW_DB", None)
        self.year = datetime.datetime.now().year

    def tearDown(self):
        records.psycopg = self._real_psycopg
        records.dict_row = self._real_dict_row
        if self._real_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._real_db_url
        if self._real_dealflow_db is not None:
            os.environ["DEALFLOW_DB"] = self._real_dealflow_db

    def sample(self, **overrides):
        raw = {"name": "Sample Deal", "asset_type": "Commercial",
               "location": "Vancouver, WA", "purchase_price": 10_000_000,
               "noi": 650_000}
        raw.update(overrides)
        return raw

    def statements(self):
        stmts = []
        for conn in self.fake.connections:
            stmts.extend(conn.statements)
        return stmts

    # --- backend selection ---

    def test_using_postgres_with_database_url(self):
        self.assertTrue(records._using_postgres())
        self.assertTrue(records._using_postgres(None))

    def test_explicit_path_stays_sqlite(self):
        self.assertFalse(records._using_postgres("/tmp/x.db"))

    def test_no_database_url_means_sqlite(self):
        del os.environ["DATABASE_URL"]
        self.assertFalse(records._using_postgres())

    def test_q_translation(self):
        self.assertEqual(records._q("a = ? AND b = ?", True),
                         "a = %s AND b = %s")
        self.assertEqual(records._q("a = ? AND b = ?", False),
                         "a = ? AND b = ?")

    def test_pg_schemas_have_no_sqlite_dialect(self):
        for schema in (records.PG_SCHEMA, records.PG_DD_ITEMS_SCHEMA,
                       records.PG_DD_DOCUMENTS_SCHEMA):
            self.assertNotIn("AUTOINCREMENT", schema)
            self.assertIn("SERIAL PRIMARY KEY", schema)
            self.assertIn("CREATE TABLE IF NOT EXISTS", schema)

    # --- end-to-end over the fake ---

    def test_submit_deal_postgres_dialect(self):
        # _connect: information_schema -> no source column -> ALTER TABLE.
        # submit: INSERT ... RETURNING id.
        self.fake.connections  # created lazily on first _connect
        # Prime the first connection's result queue before submit runs:
        # we cannot know which connection object yet, so patch connect to
        # preload results.
        orig_connect = self.fake.connect

        def primed_connect(url, **kwargs):
            conn = orig_connect(url, **kwargs)
            conn.results.append([])          # information_schema: no cols
            conn.results.append({"id": 7})   # INSERT ... RETURNING id
            return conn

        self.fake.connect = primed_connect
        deal_id = records.submit_deal(self.sample())
        self.assertRegex(deal_id, r"^AT-%d-000007$" % self.year)

        stmts = self.statements()
        self.assertTrue(stmts, "no statements were recorded")
        for sql in stmts:
            self.assertNotIn("?", sql,
                             "untranslated placeholder in: %r" % sql)
            self.assertNotIn("PRAGMA", sql)
            self.assertNotIn("AUTOINCREMENT", sql)
            self.assertNotIn("OR IGNORE", sql)
        joined = "\n".join(stmts)
        self.assertIn("RETURNING id", joined)
        self.assertIn("information_schema.columns", joined)
        self.assertIn("ON CONFLICT (deal_id, item_key) DO NOTHING", joined)
        # placeholders were translated
        self.assertIn("%s", joined)
        # every connection was committed and closed
        for conn in self.fake.connections:
            self.assertTrue(conn.committed)
            self.assertTrue(conn.closed)
        # dict_row passed through to psycopg
        self.assertTrue(self.fake.seen_kwargs)
        for kw in self.fake.seen_kwargs:
            self.assertIn("row_factory", kw)

    def test_get_deal_round_trip_over_fake(self):
        def primed_connect(url, **kwargs):
            conn = orig(url, **kwargs)
            conn.results.append([])        # information_schema
            conn.results.append(dict(FULL_ROW))  # SELECT * FROM deals
            return conn

        orig = self.fake.connect
        self.fake.connect = primed_connect
        deal = records.get_deal("AT-2026-000007")
        self.assertEqual(deal["deal_id"], "AT-2026-000007")
        self.assertEqual(deal["inputs"]["purchase_price"], 10_000_000)
        self.assertEqual(deal["status"], "NEW")
        stmts = self.statements()
        for sql in stmts:
            self.assertNotIn("?", sql)

    def test_set_status_postgres_dialect(self):
        def primed_connect(url, **kwargs):
            conn = orig(url, **kwargs)
            conn.results.append([])              # information_schema
            conn.results.append({"status": "NEW"})  # SELECT status
            return conn

        orig = self.fake.connect
        self.fake.connect = primed_connect
        self.assertEqual(
            records.set_status("AT-2026-000007", "REVIEWING"), "REVIEWING")
        joined = "\n".join(self.statements())
        self.assertIn("UPDATE deals SET status = %s", joined)
        self.assertNotIn("?", joined)

    def test_missing_psycopg_raises_helpful_error(self):
        records.psycopg = None
        with self.assertRaises(RuntimeError) as ctx:
            records._connect()
        self.assertIn("psycopg", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
