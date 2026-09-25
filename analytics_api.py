"""Charter-analytics JSON API (Flask).

Additive routes for the ported analytics: evidence log, perspectives,
scenarios, Monte Carlo simulation, decision journal, and the observer
view. The existing HTML routes in app.py are untouched; this blueprint
only adds /api/... endpoints.

Every endpoint re-reads the deal and re-underwrites from the submitted
inputs, so analytics always reflect the canonical record.
"""

from flask import Blueprint, abort, jsonify, request

import perspectives
import records
import scenarios
import simulation

analytics_api = Blueprint("analytics_api", __name__)


def _deal_or_404(deal_id):
    deal = records.get_deal(deal_id)
    if not deal:
        abort(404, description="Deal not found")
    return deal


def _json_body():
    return request.get_json(force=True, silent=True) or {}


# ---------------------------------------------------------------------------
# Evidence log
# ---------------------------------------------------------------------------

@analytics_api.route("/api/deals/<deal_id>/evidence", methods=["GET"])
def api_list_evidence(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"evidence": records.list_evidence(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/evidence", methods=["POST"])
def api_add_evidence(deal_id):
    _deal_or_404(deal_id)
    data = _json_body()
    try:
        item_id = records.add_evidence(
            deal_id,
            data.get("type"),
            data.get("content"),
            data.get("source", ""),
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify({"id": item_id, "evidence": records.list_evidence(deal_id)}), 201


# ---------------------------------------------------------------------------
# Perspectives
# ---------------------------------------------------------------------------

@analytics_api.route("/api/deals/<deal_id>/perspectives", methods=["GET"])
def api_list_perspectives(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"perspectives": records.list_perspectives(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/perspectives", methods=["POST"])
def api_run_perspectives(deal_id):
    deal = _deal_or_404(deal_id)
    data = _json_body()
    try:
        analyses = perspectives.analyze_deal(
            deal, records.list_evidence(deal_id), data.get("lenses"))
    except ValueError as exc:
        abort(400, description=str(exc))
    records.save_perspectives(deal_id, analyses)
    return jsonify({"perspectives": analyses})


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@analytics_api.route("/api/deals/<deal_id>/scenarios", methods=["GET"])
def api_list_scenarios(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"scenarios": records.list_scenarios(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/scenarios", methods=["POST"])
def api_run_scenarios(deal_id):
    deal = _deal_or_404(deal_id)
    data = _json_body()
    try:
        results = scenarios.run_scenarios(deal, data.get("scenarios"))
    except ValueError as exc:
        abort(400, description=str(exc))
    for result in results:
        records.save_scenarios(deal_id, result["name"], result["overrides"], result)
    return jsonify({"scenarios": results})


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

@analytics_api.route("/api/deals/<deal_id>/simulate", methods=["GET"])
def api_list_simulations(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"simulations": records.list_simulations(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/simulate", methods=["POST"])
def api_run_simulation(deal_id):
    deal = _deal_or_404(deal_id)
    data = _json_body()
    try:
        summary = simulation.run_simulation(
            deal,
            data.get("ranges"),
            n=data.get("n", simulation.DEFAULT_TRIALS),
            seed=data.get("seed", 42),
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    records.save_simulation(
        deal_id, data.get("ranges") or {}, summary["n_trials"],
        summary["seed"], summary)
    return jsonify({"simulation": summary})


# ---------------------------------------------------------------------------
# Decision journal
# ---------------------------------------------------------------------------

@analytics_api.route("/api/deals/<deal_id>/thesis", methods=["GET"])
def api_list_theses(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"theses": records.list_theses(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/thesis", methods=["POST"])
def api_record_thesis(deal_id):
    _deal_or_404(deal_id)
    data = _json_body()
    try:
        thesis_id = records.record_thesis(
            deal_id,
            data.get("thesis"),
            data.get("key_assumptions", []),
            data.get("expected_outcome", ""),
        )
    except KeyError as exc:
        abort(404, description=str(exc))
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify({"id": thesis_id, "theses": records.list_theses(deal_id)}), 201


@analytics_api.route("/api/deals/<deal_id>/outcomes", methods=["GET"])
def api_list_outcomes(deal_id):
    _deal_or_404(deal_id)
    return jsonify({"outcomes": records.list_outcomes(deal_id)})


@analytics_api.route("/api/deals/<deal_id>/outcomes", methods=["POST"])
def api_record_outcome(deal_id):
    _deal_or_404(deal_id)
    data = _json_body()
    try:
        outcome_id = records.record_outcome(
            deal_id,
            data.get("thesis_agreement"),
            data.get("outcome_summary"),
            data.get("actual_metrics", {}),
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify({"id": outcome_id, "outcomes": records.list_outcomes(deal_id)}), 201


@analytics_api.route("/api/journal/observer", methods=["GET"])
def api_observer():
    return jsonify(records.observer_summary())


# ---------------------------------------------------------------------------
# Errors: keep API responses JSON
# ---------------------------------------------------------------------------

@analytics_api.app_errorhandler(400)
def _bad_request(exc):
    return jsonify({"error": getattr(exc, "description", "Bad request")}), 400


@analytics_api.app_errorhandler(404)
def _not_found(exc):
    return jsonify({"error": getattr(exc, "description", "Not found")}), 404
