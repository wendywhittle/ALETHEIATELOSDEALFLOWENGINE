"""Charter-analytics port tests.

Covers the six ported modules and their Flask JSON API, adapted to the
deal-flow engine's canonical record (``inputs`` plus ``underwriting`` with
provenance), its percentage-point units (ltv 70, interest_rate 6.5,
exit_cap_rate 6.5), and its ``hold_period`` field name.

Every test runs against a throwaway SQLite database; the 131 seeded deals
and all existing behavior are untouched.
"""

import os
import tempfile
import unittest

import records
from app import app

import evidence
import journal
import perspectives
import scenarios
import simulation


FULL_INPUTS = {
    "name": "Charter Analytics Test Deal",
    "asset_type": "Multifamily",
    "location": "Vancouver, WA",
    "purchase_price": 10000000,
    "noi": 650000,
    "ltv": 70,
    "interest_rate": 6.5,
    "amortization_years": 30,
    "hold_period": 5,
    "exit_cap_rate": 6.5,
    "closing_costs": 100000,
}

THIN_INPUTS = {
    "name": "Thin Deal",
    "asset_type": "Multifamily",
    "location": "Vancouver, WA",
    "purchase_price": 10000000,
}

ANALYSIS_KEYS = (
    "belief", "why", "supporting_evidence", "contradicting_evidence",
    "key_assumptions", "uncertainty", "what_would_change_conclusion",
)


class CharterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DEALFLOW_DB"] = os.path.join(self.tmp.name, "test.db")
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        del os.environ["DEALFLOW_DB"]
        self.tmp.cleanup()

    def make_deal(self, inputs=None):
        return records.submit_deal(dict(inputs or FULL_INPUTS))

    def get_deal(self, deal_id):
        return records.get_deal(deal_id)

    # ------------------------------------------------------------------
    # Evidence log
    # ------------------------------------------------------------------

    def test_evidence_roundtrip(self):
        deal_id = self.make_deal()
        item_id = records.add_evidence(
            deal_id, "sourced", "Broker OM states 94% occupancy.", "broker_om.pdf")
        self.assertIsInstance(item_id, int)
        items = records.list_evidence(deal_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "sourced")
        self.assertEqual(items[0]["content"], "Broker OM states 94% occupancy.")
        self.assertEqual(items[0]["source"], "broker_om.pdf")
        self.assertTrue(items[0]["created_at"])

    def test_evidence_rejects_bad_type(self):
        deal_id = self.make_deal()
        with self.assertRaises(ValueError):
            records.add_evidence(deal_id, "vibes", "Feels good.")
        with self.assertRaises(ValueError):
            evidence.validate_evidence("rumor", "Heard it somewhere.")

    def test_evidence_rejects_empty(self):
        deal_id = self.make_deal()
        with self.assertRaises(ValueError):
            records.add_evidence(deal_id, "observed", "   ")

    # ------------------------------------------------------------------
    # Perspectives
    # ------------------------------------------------------------------

    def test_all_lenses_produce_required_keys(self):
        deal = self.get_deal(self.make_deal())
        analyses = perspectives.analyze_deal(deal, [])
        self.assertEqual(set(analyses), {"Bull", "Bear", "Quant", "Skeptic"})
        for lens, analysis in analyses.items():
            for key in ANALYSIS_KEYS:
                self.assertIn(key, analysis, f"{lens} missing {key}")
            self.assertEqual(analysis["lens"], lens)

    def test_dissent_is_preserved_not_merged(self):
        deal = self.get_deal(self.make_deal())
        analyses = perspectives.analyze_deal(deal, [], lenses=["Bull", "Bear"])
        self.assertNotEqual(analyses["Bull"]["belief"], analyses["Bear"]["belief"])
        records.save_perspectives(deal["deal_id"], analyses)
        rows = records.list_perspectives(deal["deal_id"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["lens"] for r in rows}, {"Bull", "Bear"})

    def test_quant_is_honest_when_incomplete(self):
        deal = self.get_deal(self.make_deal(THIN_INPUTS))
        analyses = perspectives.analyze_deal(deal, [])
        self.assertIn("incomplete", analyses["Quant"]["belief"].lower())
        self.assertTrue(any("unknown" in u.lower() for u in analyses["Bull"]["uncertainty"]))

    def test_unknown_lens_rejected(self):
        deal = self.get_deal(self.make_deal())
        with self.assertRaises(ValueError):
            perspectives.analyze_deal(deal, [], lenses=["Moonshot"])

    def test_perspectives_persist_and_list(self):
        deal = self.get_deal(self.make_deal())
        records.save_perspectives(deal["deal_id"], perspectives.analyze_deal(deal, []))
        records.save_perspectives(
            deal["deal_id"], perspectives.analyze_deal(deal, [], lenses=["Bull"]))
        rows = records.list_perspectives(deal["deal_id"])
        # History is append-only: the second run adds a row, never rewrites.
        self.assertEqual(len(rows), 5)

    def test_bear_computes_fragility(self):
        deal = self.get_deal(self.make_deal())
        bear = perspectives.analyze_deal(deal, [], lenses=["Bear"])["Bear"]
        self.assertTrue(any("Break-even NOI" in w for w in bear["why"]))

    def test_skeptic_audits_soft_evidence(self):
        deal_id = self.make_deal()
        records.add_evidence(deal_id, "assumed", "Rents will grow 3% annually.")
        deal = self.get_deal(deal_id)
        skeptic = perspectives.analyze_deal(
            deal, records.list_evidence(deal_id), lenses=["Skeptic"])["Skeptic"]
        self.assertTrue(any("Rents will grow" in c for c in skeptic["contradicting_evidence"]))

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------

    def test_default_scenarios_bull_bear_ordering(self):
        deal = self.get_deal(self.make_deal())
        results = scenarios.run_scenarios(deal)
        by_name = {r["name"]: r for r in results}
        self.assertEqual(set(by_name), {"base", "bull", "bear"})
        cap = {k: v["underwrite"]["cap_rate"] for k, v in by_name.items()}
        self.assertGreaterEqual(cap["bull"], cap["base"])
        self.assertGreaterEqual(cap["base"], cap["bear"])
        # The bull perturbation is described in the result's own notes.
        self.assertTrue(any("noi" in n for n in by_name["bull"].get("notes", [])))

    def test_custom_scenario_override_respected(self):
        deal = self.get_deal(self.make_deal())
        base_irr = scenarios.run_scenarios(deal)[0]["underwrite"]["irr"]
        custom = scenarios.run_scenarios(
            deal, [{"name": "long_hold", "overrides": {"hold_period": 10}}])
        self.assertEqual(custom[0]["name"], "long_hold")
        self.assertEqual(custom[0]["overrides"], {"hold_period": 10})
        self.assertNotAlmostEqual(custom[0]["underwrite"]["irr"], base_irr)

    def test_ranged_inputs_yield_intervals_not_points(self):
        import validation
        numeric = {k: v for k, v in FULL_INPUTS.items()
                   if k in validation.INPUT_FIELDS}
        record = {"inputs": dict(numeric, noi=[500000, 700000])}
        results = scenarios.run_scenarios(record)
        base = results[0]
        self.assertIn("range", base)
        self.assertAlmostEqual(base["range"]["lo"]["cap_rate"], 0.05)
        self.assertAlmostEqual(base["range"]["hi"]["cap_rate"], 0.07)

    def test_invalid_range_rejected(self):
        with self.assertRaises(ValueError):
            scenarios.as_range([5, 3])
        deal = self.get_deal(self.make_deal())
        with self.assertRaises(ValueError):
            scenarios.run_scenarios(
                deal, [{"name": "bad", "overrides": {"noi": [700000, 500000]}}])

    def test_scenarios_persist(self):
        deal = self.get_deal(self.make_deal())
        results = scenarios.run_scenarios(deal)
        for result in results:
            records.save_scenarios(deal["deal_id"], result["name"], result["overrides"], result)
        rows = records.list_scenarios(deal["deal_id"])
        self.assertEqual([r["name"] for r in rows], ["base", "bull", "bear"])
        self.assertIn("cap_rate", rows[0]["results"]["underwrite"])

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def test_simulation_is_deterministic(self):
        deal = self.get_deal(self.make_deal())
        ranges = {"noi": [500000, 800000], "exit_cap_rate": [5.0, 7.0]}
        first = simulation.run_simulation(deal, ranges, n=200, seed=7)
        second = simulation.run_simulation(deal, ranges, n=200, seed=7)
        self.assertEqual(first, second)

    def test_simulation_percentiles_ordered_and_bounded(self):
        deal = self.get_deal(self.make_deal())
        summary = simulation.run_simulation(
            deal, {"noi": [500000, 800000], "exit_cap_rate": [5.0, 7.0]},
            n=300, seed=7)
        self.assertEqual(summary["n_trials"], 300)
        self.assertEqual(summary["seed"], 7)
        self.assertEqual(summary["failed_trials"], 0)
        cap = summary["metric_summaries"]["cap_rate"]
        for lo, hi in (("p5", "p25"), ("p25", "p50"), ("p50", "p75"), ("p75", "p95")):
            self.assertLessEqual(cap[lo], cap[hi])
        self.assertAlmostEqual(cap["p5"], 0.05, delta=0.005)
        self.assertAlmostEqual(cap["p95"], 0.08, delta=0.005)
        for key, prob in summary["guardrail_probabilities"].items():
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)
        # The independent-draw limitation is disclosed in the summary itself.
        self.assertTrue(any("independent" in lim.lower() and "correlat" in lim.lower()
                            for lim in summary["limitations"]))
        self.assertIn("uniform", summary["draw_distribution"])

    def test_simulation_rejects_bad_config(self):
        deal = self.get_deal(self.make_deal())
        with self.assertRaises(ValueError):
            simulation.run_simulation(deal, {}, n=0)
        with self.assertRaises(ValueError):
            simulation.run_simulation(deal, {"moonshot": [1, 2]}, n=50)
        with self.assertRaises(ValueError):
            simulation.run_simulation(deal, {"noi": [800000, 500000]}, n=50)

    def test_simulation_persists(self):
        deal = self.get_deal(self.make_deal())
        ranges = {"noi": [500000, 800000]}
        summary = simulation.run_simulation(deal, ranges, n=200, seed=7)
        records.save_simulation(deal["deal_id"], ranges, 200, 7, summary)
        rows = records.list_simulations(deal["deal_id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seed"], 7)
        self.assertEqual(rows[0]["summary"]["n_trials"], 200)

    # ------------------------------------------------------------------
    # Decision journal
    # ------------------------------------------------------------------

    def test_thesis_requires_pursue_status(self):
        deal_id = self.make_deal()
        with self.assertRaises(ValueError):
            records.record_thesis(deal_id, "This will outperform.")
        records.set_status(deal_id, "REVIEWING")
        with self.assertRaises(ValueError):
            records.record_thesis(deal_id, "This will outperform.")
        records.set_status(deal_id, "PURSUE")
        thesis_id = records.record_thesis(
            deal_id, "This will outperform.",
            key_assumptions=["NOI holds"], expected_outcome="IRR above 12%")
        self.assertIsInstance(thesis_id, int)
        # journal.check_thesis_allowed raises KeyError for unknown deals.
        with self.assertRaises(KeyError):
            journal.check_thesis_allowed(None, "AT-2099-000001")

    def test_thesis_outcome_observer_flow(self):
        deal_id = self.make_deal()
        records.set_status(deal_id, "REVIEWING")
        records.set_status(deal_id, "PURSUE")
        records.record_thesis(deal_id, "Cap rate compression thesis.",
                              expected_outcome="Exit below 6% cap")
        records.record_outcome(deal_id, "agree", "Exited at 5.8% cap.",
                               {"exit_cap_rate": 5.8})
        theses = records.list_theses(deal_id)
        outcomes = records.list_outcomes(deal_id)
        self.assertEqual(len(theses), 1)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["thesis_agreement"], "agree")
        summary = records.observer_summary()
        self.assertEqual(summary["counts"]["theses"], 1)
        self.assertEqual(summary["counts"]["resolved"], 1)
        self.assertEqual(summary["hit_rate"], 1.0)
        self.assertEqual(summary["theses"][0]["deal_id"], deal_id)

    def test_outcome_rejects_bad_agreement(self):
        deal_id = self.make_deal()
        with self.assertRaises(ValueError):
            records.record_outcome(deal_id, "mostly", "It went fine.")

    # ------------------------------------------------------------------
    # JSON API
    # ------------------------------------------------------------------

    def test_api_evidence_flow(self):
        deal_id = self.make_deal()
        resp = self.client.post(
            f"/api/deals/{deal_id}/evidence",
            json={"type": "observed", "content": "Walked the property.",
                  "source": "site visit"})
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(f"/api/deals/{deal_id}/evidence")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["evidence"]), 1)
        resp = self.client.post(
            f"/api/deals/{deal_id}/evidence",
            json={"type": "vibes", "content": "Feels right."})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())
        resp = self.client.get("/api/deals/AT-2099-000001/evidence")
        self.assertEqual(resp.status_code, 404)

    def test_api_perspectives_flow(self):
        deal_id = self.make_deal()
        resp = self.client.post(
            f"/api/deals/{deal_id}/perspectives",
            json={"lenses": ["Bull", "Skeptic"]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(set(body["perspectives"]), {"Bull", "Skeptic"})
        resp = self.client.post(
            f"/api/deals/{deal_id}/perspectives", json={"lenses": ["Moonshot"]})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(f"/api/deals/{deal_id}/perspectives")
        self.assertEqual(len(resp.get_json()["perspectives"]), 2)

    def test_api_scenarios_flow(self):
        deal_id = self.make_deal()
        resp = self.client.post(
            f"/api/deals/{deal_id}/scenarios",
            json={"scenarios": [{"name": "stress", "overrides": {"noi": 400000}}]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["scenarios"][0]["name"], "stress")
        self.assertLess(body["scenarios"][0]["underwrite"]["cap_rate"], 0.065)
        resp = self.client.get(f"/api/deals/{deal_id}/scenarios")
        self.assertEqual(len(resp.get_json()["scenarios"]), 1)
        resp = self.client.post(
            f"/api/deals/{deal_id}/scenarios",
            json={"scenarios": [{"name": "bad", "overrides": {"noi": "a lot"}}]})
        self.assertEqual(resp.status_code, 400)

    def test_api_simulate_flow(self):
        deal_id = self.make_deal()
        resp = self.client.post(
            f"/api/deals/{deal_id}/simulate",
            json={"ranges": {"noi": [500000, 800000]}, "n": 200, "seed": 7})
        self.assertEqual(resp.status_code, 200)
        summary = resp.get_json()["simulation"]
        self.assertEqual(summary["n_trials"], 200)
        self.assertIn("cap_rate", summary["metric_summaries"])
        self.assertTrue(summary["limitations"])
        resp = self.client.get(f"/api/deals/{deal_id}/simulate")
        self.assertEqual(len(resp.get_json()["simulations"]), 1)

    def test_api_thesis_guard_and_observer(self):
        deal_id = self.make_deal()
        # NEW status: the thesis guard holds on the API too.
        resp = self.client.post(
            f"/api/deals/{deal_id}/thesis", json={"thesis": "Too early."})
        self.assertEqual(resp.status_code, 400)
        records.set_status(deal_id, "REVIEWING")
        records.set_status(deal_id, "PURSUE")
        resp = self.client.post(
            f"/api/deals/{deal_id}/thesis",
            json={"thesis": "Strong in-place cash flow.",
                  "key_assumptions": ["NOI verified"],
                  "expected_outcome": "Hold 5 years"})
        self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            f"/api/deals/{deal_id}/outcomes",
            json={"thesis_agreement": "agree",
                  "outcome_summary": "Cash flow held up.",
                  "actual_metrics": {"cash_on_cash": 0.09}})
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get("/api/journal/observer")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["counts"]["theses"], 1)
        self.assertEqual(body["hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# PostgreSQL dialect coverage (mocked psycopg, no live server)
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

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
    def __init__(self):
        self.connections = []

    def connect(self, url, **kwargs):
        conn = FakeConnection()
        self.connections.append(conn)
        return conn


PG_DEAL_ROW = {
    "deal_id": "AT-2026-000001",
    "name": "PG Deal",
    "asset_type": "Multifamily",
    "location": "Vancouver, WA",
    "source": "",
    "inputs_json": '{"purchase_price": 10000000, "noi": 650000}',
    "underwriting_json": "{}",
    "contact_json": "{}",
    "status": "PURSUE",
    "artifact_path": None,
    "created_at": "2026-09-25T00:00:00+00:00",
    "updated_at": "2026-09-25T00:00:00+00:00",
}


class AnalyticsPostgresCase(unittest.TestCase):
    """Analytics tables over the PostgreSQL path, with psycopg faked.

    Asserts the new DDL and queries are valid Postgres dialect (SERIAL,
    %s placeholders, INSERT ... RETURNING) and that every connection is
    committed and closed.
    """

    def setUp(self):
        self._real_psycopg = records.psycopg
        self._real_dict_row = records.dict_row
        self._real_db_url = os.environ.get("DATABASE_URL")
        self._real_dealflow_db = os.environ.pop("DEALFLOW_DB", None)
        self.fake = FakePsycopg()
        records.psycopg = self.fake
        records.dict_row = object()
        os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/dealflow"

    def tearDown(self):
        records.psycopg = self._real_psycopg
        records.dict_row = self._real_dict_row
        if self._real_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/dealflow"
        if self._real_dealflow_db is not None:
            os.environ["DEALFLOW_DB"] = self._real_dealflow_db

    def prime_shared(self, results):
        """All fake connections pop from one shared result queue, in order."""
        shared = list(results)
        orig = self.fake.connect

        def primed(url, **kwargs):
            conn = orig(url, **kwargs)
            conn.results = shared
            return conn

        self.fake.connect = primed

    def statements(self):
        stmts = []
        for conn in self.fake.connections:
            stmts.extend(conn.statements)
        return stmts

    def assert_pg_clean(self, stmts):
        for sql in stmts:
            self.assertNotIn("?", sql, "untranslated placeholder in: %r" % sql)
            self.assertNotIn("AUTOINCREMENT", sql)
            self.assertNotIn("PRAGMA", sql)
        for conn in self.fake.connections:
            self.assertTrue(conn.committed)
            self.assertTrue(conn.closed)

    def test_pg_analytics_schema_dialect(self):
        schema = records.PG_ANALYTICS_SCHEMA
        self.assertNotIn("AUTOINCREMENT", schema)
        for table in ("evidence", "perspectives", "scenarios",
                      "simulations", "theses", "outcomes"):
            self.assertIn("CREATE TABLE IF NOT EXISTS %s" % table, schema)
            self.assertIn("idx_%s_deal" % table, schema)
        self.assertIn("SERIAL PRIMARY KEY", schema)

    def test_pg_ddl_runs_on_connect(self):
        self.prime_shared([[]])
        conn = records._connect()
        conn.close()
        joined = "\n".join(self.statements())
        self.assertIn("CREATE TABLE IF NOT EXISTS evidence", joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS theses", joined)
        self.assert_pg_clean(self.statements())

    def test_add_evidence_pg(self):
        self.prime_shared([[], {"id": 3}])
        item_id = records.add_evidence(
            "AT-2026-000001", "observed", "Walked the property.", "site visit")
        self.assertEqual(item_id, 3)
        joined = "\n".join(self.statements())
        self.assertIn("INSERT INTO evidence", joined)
        self.assertIn("RETURNING id", joined)
        self.assertIn("%s", joined)
        self.assert_pg_clean(self.statements())

    def test_save_perspectives_pg(self):
        self.prime_shared([[], {"id": 1}, {"id": 2}])
        new_ids = records.save_perspectives(
            "AT-2026-000001",
            {"Bull": {"lens": "Bull", "belief": "b"},
             "Bear": {"lens": "Bear", "belief": "c"}})
        self.assertEqual(new_ids, [1, 2])
        joined = "\n".join(self.statements())
        self.assertIn("INSERT INTO perspectives", joined)
        self.assertIn("RETURNING id", joined)
        self.assert_pg_clean(self.statements())

    def test_record_thesis_pg_guard_and_dialect(self):
        # Guard first: a non-PURSUE deal never reaches the INSERT.
        row = dict(PG_DEAL_ROW, status="NEW")
        self.prime_shared([[], row])
        with self.assertRaises(ValueError):
            records.record_thesis("AT-2026-000001", "Too early.")
        self.assertNotIn("INSERT INTO theses", "\n".join(self.statements()))

        # PURSUE deal: INSERT ... RETURNING id over translated placeholders.
        self.fake.connections.clear()
        self.prime_shared([[], dict(PG_DEAL_ROW), [], {"id": 9}])
        thesis_id = records.record_thesis(
            "AT-2026-000001", "Strong cash flow.", ["NOI verified"], "Hold")
        self.assertEqual(thesis_id, 9)
        joined = "\n".join(self.statements())
        self.assertIn("INSERT INTO theses", joined)
        self.assertIn("RETURNING id", joined)
        self.assert_pg_clean(self.statements())

    def test_record_outcome_pg(self):
        self.prime_shared([[], {"id": 4}])
        outcome_id = records.record_outcome(
            "AT-2026-000001", "disagree", "NOI fell short.", {"noi": 400000})
        self.assertEqual(outcome_id, 4)
        joined = "\n".join(self.statements())
        self.assertIn("INSERT INTO outcomes", joined)
        self.assertIn("RETURNING id", joined)
        self.assert_pg_clean(self.statements())

    def test_observer_summary_pg(self):
        thesis = {"id": 1, "deal_id": "AT-2026-000001", "thesis": "T",
                  "key_assumptions_json": "[]", "expected_outcome": "",
                  "created_at": "2026-09-25T10:00:00.123456+00:00"}
        outcome = {"id": 1, "deal_id": "AT-2026-000001",
                   "thesis_agreement": "agree", "outcome_summary": "O",
                   "actual_metrics_json": "{}",
                   "created_at": "2026-09-25T10:00:01.654321+00:00"}
        self.prime_shared([[], [thesis], [], [outcome], [], dict(PG_DEAL_ROW)])
        summary = records.observer_summary()
        self.assertEqual(summary["counts"]["theses"], 1)
        self.assertEqual(summary["counts"]["resolved"], 1)
        self.assertEqual(summary["hit_rate"], 1.0)
        self.assertEqual(summary["theses"][0]["deal_name"], "PG Deal")
        self.assert_pg_clean(self.statements())
