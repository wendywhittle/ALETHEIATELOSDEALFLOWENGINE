"""AletheiaTelos DealFlow Engine — Flask application.

Analytical and administrative only. This application never executes trades,
purchases, moves capital, accesses brokerage accounts, or makes investment
decisions. It produces deterministic analysis from user-supplied numbers;
humans make all decisions.

Routes:
  GET  /                        landing page
  GET  /intake                  deal intake form
  POST /intake                  validate -> canonical record -> underwriting
  GET  /results/<deal_id>       analytical result for the submitter
  GET  /pipeline                internal deal pipeline (optional ?status=)
  GET  /deals/<deal_id>         deal detail (the deal is the workspace)
  POST /deals/<deal_id>/status  human-controlled status change
  GET  /deals/<deal_id>/excel   generated Excel artifact download
  POST /deals/<deal_id>/dd/status   DD item status change (human-controlled)
  POST /deals/<deal_id>/dd/notes    DD item notes
  POST /deals/<deal_id>/dd/upload   DD document upload (max 50 MB)
  GET  /deals/<deal_id>/dd/docs/<doc_id>  DD document download
"""

import os

from flask import (Flask, abort, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

import excel_gen
import records
import validation

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB per upload


def _dd_upload_root():
    return os.path.join(
        os.environ.get("DEALFLOW_ARTIFACTS",
                       os.path.join(records.REPO_DIR, "artifacts")),
        "dd")


# ---------- display helpers ----------

def _fmt_money(v):
    return "—" if v is None else "$%s" % f"{v:,.0f}"


def _fmt_pct(v):
    return "—" if v is None else "%.2f%%" % (v * 100.0)


def _fmt_num(v):
    return "—" if v is None else f"{v:,.2f}"


def _fmt_any(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return _fmt_num(v)
    return str(v)


app.jinja_env.filters["money"] = _fmt_money
app.jinja_env.filters["pct"] = _fmt_pct
app.jinja_env.filters["num"] = _fmt_num
app.jinja_env.filters["any"] = _fmt_any

METRIC_ORDER = ["noi", "egi", "cap_rate", "loan_amount", "initial_equity",
                "debt_service", "dscr", "cash_flow", "cash_on_cash",
                "exit_value", "irr", "equity_multiple"]
METRIC_LABELS = {
    "noi": "NOI (annual)",
    "egi": "EGI (annual)",
    "cap_rate": "Cap rate",
    "loan_amount": "Loan amount",
    "initial_equity": "Initial equity",
    "debt_service": "Annual debt service",
    "dscr": "DSCR",
    "cash_flow": "Annual cash flow",
    "cash_on_cash": "Cash-on-cash return",
    "exit_value": "Exit value",
    "irr": "IRR (annual)",
    "equity_multiple": "Equity multiple",
}
METRIC_KIND = {
    "cap_rate": "pct", "cash_on_cash": "pct", "irr": "pct",
    "dscr": "num", "equity_multiple": "num",
}


def _split_metrics(underwriting):
    available, missing = [], []
    for key in METRIC_ORDER:
        m = underwriting.get(key, {})
        entry = {"key": key, "label": METRIC_LABELS[key],
                 "kind": METRIC_KIND.get(key, "money"), **m}
        if m.get("value") is None:
            missing.append(entry)
        else:
            available.append(entry)
    return available, missing


# ---------- public: landing / intake / results ----------

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/intake", methods=["GET", "POST"])
def intake():
    if request.method == "GET":
        return render_template("intake.html", errors=[], form={})
    form = {k: v for k, v in request.form.items()}
    cleaned, errors = validation.validate(form)
    if errors:
        return render_template("intake.html", errors=errors, form=form)
    try:
        deal_id = records.submit_deal(cleaned)
    except ValueError as exc:  # defensive; validation already ran
        return render_template(
            "intake.html",
            errors=[{"field": "", "code": "invalid", "message": str(exc)}],
            form=form)
    return redirect(url_for("results", deal_id=deal_id))


@app.route("/results/<deal_id>")
def results(deal_id):
    deal = records.get_deal(deal_id)
    if not deal:
        abort(404)
    available, missing = _split_metrics(deal["underwriting"])
    return render_template("results.html", deal=deal,
                           available=available, missing=missing)


# ---------- internal: pipeline / detail / review ----------

@app.route("/pipeline")
def pipeline():
    status = request.args.get("status") or None
    if status and status not in records.STATUSES:
        abort(400)
    deals = records.list_deals(status=status)
    dd_map = records.dd_progress_all()
    return render_template("pipeline.html", deals=deals,
                           statuses=records.STATUSES, active=status,
                           dd_map=dd_map)


def _dd_context(deal_id):
    """Due-diligence data for the deal detail page."""
    items = records.list_dd_items(deal_id)
    groups = []
    for category in records.DD_CATEGORIES:
        cat_items = [i for i in items if i["category"] == category]
        if cat_items:
            groups.append({"category": category, "items": cat_items})
    docs_by_item = {}
    for doc in records.list_dd_documents(deal_id):
        docs_by_item.setdefault(doc["item_key"], []).append(doc)
    cleared, total = records.dd_progress(deal_id)
    return {
        "dd_groups": groups,
        "dd_docs_by_item": docs_by_item,
        "dd_cleared": cleared,
        "dd_total": total,
        "dd_statuses": records.DD_STATUSES,
    }


@app.route("/deals/<deal_id>")
def detail(deal_id):
    deal = records.get_deal(deal_id)
    if not deal:
        abort(404)
    available, missing = _split_metrics(deal["underwriting"])
    next_statuses = sorted(records.TRANSITIONS.get(deal["status"], ()))
    return render_template("detail.html", deal=deal, available=available,
                           missing=missing, next_statuses=next_statuses,
                           input_fields=validation.INPUT_FIELDS,
                           **_dd_context(deal_id))


@app.route("/deals/<deal_id>/status", methods=["POST"])
def change_status(deal_id):
    deal = records.get_deal(deal_id)
    if not deal:
        abort(404)
    new_status = (request.form.get("status") or "").strip()
    try:
        records.set_status(deal_id, new_status)
    except ValueError as exc:
        available, missing = _split_metrics(deal["underwriting"])
        return render_template(
            "detail.html", deal=deal, available=available, missing=missing,
            next_statuses=sorted(records.TRANSITIONS.get(deal["status"], ())),
            input_fields=validation.INPUT_FIELDS,
            status_error=str(exc), **_dd_context(deal_id)), 400
    return redirect(url_for("detail", deal_id=deal_id))


@app.route("/deals/<deal_id>/excel")
def download_excel(deal_id):
    deal = records.get_deal(deal_id)
    if not deal:
        abort(404)
    deal["dd_items"] = records.list_dd_items(deal_id)
    path = excel_gen.generate_excel(deal)
    records.set_artifact(deal_id, os.path.relpath(path, excel_gen.REPO_DIR))
    return send_file(path, as_attachment=True,
                     download_name="%s.xlsx" % deal_id)


# ---------- due diligence ----------

@app.route("/deals/<deal_id>/dd/status", methods=["POST"])
def dd_status(deal_id):
    if not records.get_deal(deal_id):
        abort(404)
    item_key = (request.form.get("item_key") or "").strip()
    status = (request.form.get("status") or "").strip()
    try:
        records.set_dd_status(deal_id, item_key, status)
    except ValueError as exc:
        return str(exc), 400
    return redirect(url_for("detail", deal_id=deal_id))


@app.route("/deals/<deal_id>/dd/notes", methods=["POST"])
def dd_notes(deal_id):
    if not records.get_deal(deal_id):
        abort(404)
    item_key = (request.form.get("item_key") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    try:
        records.set_dd_notes(deal_id, item_key, notes)
    except ValueError as exc:
        return str(exc), 400
    return redirect(url_for("detail", deal_id=deal_id))


@app.route("/deals/<deal_id>/dd/upload", methods=["POST"])
def dd_upload(deal_id):
    if not records.get_deal(deal_id):
        abort(404)
    item_key = (request.form.get("item_key") or "").strip()
    if not records.dd_item_exists(deal_id, item_key):
        return "Unknown due-diligence item.", 400
    f = request.files.get("document")
    if not f or not f.filename:
        return "Choose a file to upload.", 400
    filename = secure_filename(f.filename)
    if not filename:
        return "Invalid file name.", 400
    target_dir = os.path.join(_dd_upload_root(), deal_id, item_key)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, filename)
    base, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(target):
        i += 1
        target = os.path.join(target_dir, "%s-%d%s" % (base, i, ext))
    f.save(target)
    stored_rel = os.path.relpath(target, records.REPO_DIR)
    note = (request.form.get("note") or "").strip()
    records.add_dd_document(deal_id, item_key, f.filename, stored_rel, note)
    return redirect(url_for("detail", deal_id=deal_id))


@app.route("/deals/<deal_id>/dd/docs/<int:doc_id>")
def dd_download(deal_id, doc_id):
    doc = records.get_dd_document(doc_id)
    if not doc or doc["deal_id"] != deal_id:
        abort(404)
    path = os.path.join(records.REPO_DIR, doc["stored_path"])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=doc["filename"])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
