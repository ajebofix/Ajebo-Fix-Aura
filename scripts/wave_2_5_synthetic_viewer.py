"""Read-only browser viewer for Aura's isolated Wave 2.5 synthetic cohort.

This service is deliberately separate from real Aura production. It exposes only
rows belonging to the explicit `synthetic_wave_2_5` validation dataset and the
synthetic recurrence-gate result. There are no mutation routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from app import app as aura_app
from extensions import db
from models import Car, CarFault, CarOwnership, User, VehicleEvent
from scripts.audit_predictive_readiness import open_read_only_connection
from scripts.seed_wave_2_5_synthetic_cohort import (
    SCENARIOS,
    SYNTHETIC_DATASET,
    SYNTHETIC_SOURCE,
)
from scripts.validate_wave_2_5_synthetic_cohort import (
    build_synthetic_validation_result,
)


AS_OF = datetime(2026, 9, 12, 0, 0, 0)
VIEWER_USERNAME = os.environ.get("SYNTHETIC_VIEWER_USERNAME", "ajebofix")
VIEWER_PASSWORD = os.environ.get("SYNTHETIC_VIEWER_PASSWORD", "")

viewer_app = Flask(__name__)
viewer_app.config["JSON_SORT_KEYS"] = False


PAGE_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>Aura · Synthetic Dataset Viewer</title>
  <style>
    :root { color-scheme: dark; --bg:#0b0c0e; --panel:#14161a; --line:#2a2e35; --text:#f2f0e9; --muted:#a7abb3; --ok:#82d39b; --warn:#f1c76d; --bad:#ef8e8e; --accent:#d9d4c8; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    .wrap { max-width:1180px; margin:0 auto; padding:22px; }
    .banner { background:#291d08; border:1px solid #6f5016; color:#f5d98f; padding:13px 16px; border-radius:12px; font-weight:700; letter-spacing:.025em; }
    h1 { margin:28px 0 5px; font-family: Georgia, serif; font-size:clamp(30px,5vw,54px); font-weight:500; }
    h2 { font-family: Georgia, serif; font-weight:500; margin:32px 0 14px; }
    p { color:var(--muted); line-height:1.55; }
    .topline { display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between; }
    .links a { color:var(--text); text-decoration:none; border-bottom:1px solid var(--muted); margin-left:14px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-top:18px; }
    .metric,.scenario { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:16px; }
    .metric .v { font-size:30px; font-weight:750; margin-top:8px; }
    .metric .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
    .status { display:inline-flex; align-items:center; gap:7px; border:1px solid var(--line); padding:7px 10px; border-radius:999px; font-size:13px; }
    .status.ok { color:var(--ok); }.status.warn { color:var(--warn); }
    .scenario { margin:14px 0; padding:18px; }
    .scenario-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .scenario h3 { margin:0 0 7px; font-size:20px; }
    .meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:9px 18px; margin:18px 0; }
    .meta div { border-top:1px solid var(--line); padding-top:9px; }
    .meta b { display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.07em; margin-bottom:3px; }
    .timeline { list-style:none; padding:0; margin:18px 0 0; }
    .timeline li { display:grid; grid-template-columns:150px 1fr; gap:14px; border-top:1px solid var(--line); padding:12px 0; }
    .timeline time { color:var(--muted); font-size:13px; }
    code { background:#0e1013; border:1px solid var(--line); border-radius:6px; padding:2px 5px; color:#e6e2d8; }
    .boundary { background:#101a14; border:1px solid #294c34; color:#a8dfb6; padding:14px; border-radius:12px; margin-top:18px; }
    .footer { color:#777d87; padding:36px 0 18px; font-size:12px; }
    @media (max-width:620px) { .wrap{padding:15px}.timeline li{grid-template-columns:1fr;gap:4px}.links a{margin:0 12px 0 0} }
  </style>
</head>
<body>
<div class="wrap">
  <div class="banner">SYNTHETIC TEST DATA — NOT REAL CUSTOMERS · NOT PRODUCTION EVIDENCE</div>
  <div class="topline">
    <div>
      <h1>Aura Synthetic Dataset Viewer</h1>
      <p>Wave 2.5 longitudinal recurrence-gate validation · read-only isolated database.</p>
    </div>
    <div class="links"><a href="/dataset.json">dataset.json</a><a href="/validation.json">validation.json</a></div>
  </div>

  <div class="boundary">
    Synthetic validation status: <strong>{{ validation.status }}</strong> · underlying gate logic: <strong>{{ validation.underlying_gate_logic_result }}</strong>.<br>
    This validates engineering behavior only. It does not establish real-world predictive readiness.
  </div>

  <div class="grid">
    <div class="metric"><div class="k">Resolved episodes</div><div class="v">{{ validation.episodes.resolved_episodes_total }}</div></div>
    <div class="metric"><div class="k">Completed 90-day windows</div><div class="v">{{ validation.episodes.completed_90_day_windows }}</div></div>
    <div class="metric"><div class="k">Positive recurrences</div><div class="v">{{ validation.episodes.positive_recurrence }}</div></div>
    <div class="metric"><div class="k">Observed non-recurrence</div><div class="v">{{ validation.episodes.negative_observed }}</div></div>
    <div class="metric"><div class="k">Censored episodes</div><div class="v">{{ validation.episodes.censored_total }}</div></div>
    <div class="metric"><div class="k">Integrity constraints</div><div class="v">{{ validation.integrity_constraints|length }}</div></div>
  </div>

  <h2>Exactly what Aura evaluated</h2>
  {% for item in scenarios %}
  <section class="scenario">
    <div class="scenario-head">
      <div>
        <h3>{{ item.scenario_id }}</h3>
        <div>{{ item.vehicle }}</div>
      </div>
      <div class="status {{ 'warn' if item.classification == 'censored' else 'ok' }}">{{ item.classification }}</div>
    </div>
    <div class="meta">
      <div><b>Synthetic owner</b>{{ item.owner }}</div>
      <div><b>VIN</b><code>{{ item.vin }}</code></div>
      <div><b>Plate</b><code>{{ item.plate }}</code></div>
      <div><b>Concern category</b>{{ item.category }}</div>
      <div><b>Reported</b>{{ item.reported_at }}</div>
      <div><b>Resolved</b>{{ item.resolved_at }}</div>
      <div><b>Reopened</b>{{ item.reopened_at or '—' }}</div>
      <div><b>Follow-up at gate</b>{{ item.followup_days }} days</div>
      <div><b>Current synthetic concern state</b>{{ item.concern_status }}</div>
      <div><b>Odometer snapshot</b>{{ item.mileage }} km</div>
    </div>
    <ul class="timeline">
      {% for event in item.events %}
      <li>
        <time>{{ event.occurred_at }}</time>
        <div><strong>{{ event.event_type }}</strong><br><span style="color:var(--muted)">{{ event.previous_state or '∅' }} → {{ event.new_state or '∅' }} · {{ event.progression_direction }} · source <code>{{ event.source }}</code></span></div>
      </li>
      {% endfor %}
    </ul>
  </section>
  {% endfor %}

  <div class="footer">Dataset <code>{{ dataset }}</code> · source marker <code>{{ source_marker }}</code> · deterministic gate cutoff {{ as_of }} UTC.</div>
</div>
</body>
</html>
"""


def _auth_ok() -> bool:
    if not VIEWER_PASSWORD:
        return False
    auth = request.authorization
    if auth is None:
        return False
    return hmac.compare_digest(auth.username or "", VIEWER_USERNAME) and hmac.compare_digest(
        auth.password or "", VIEWER_PASSWORD
    )


@viewer_app.before_request
def require_viewer_auth():
    if request.path == "/healthz":
        return None
    if _auth_ok():
        return None
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Aura Synthetic Dataset Viewer"'},
    )


@viewer_app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@viewer_app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "synthetic_only": True})


def _classification(scenario) -> tuple[str, int]:
    followup_days = max(0, (AS_OF - scenario.resolved_at).days)
    if scenario.reopened_at is not None:
        if scenario.reopened_at <= scenario.resolved_at + timedelta(days=90):
            return "positive recurrence", followup_days
        return "reopened after 90-day window", followup_days
    if AS_OF >= scenario.resolved_at + timedelta(days=90):
        return "observed non-recurrence", followup_days
    return "censored", followup_days


def _fmt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d %b %Y · %H:%M")


def _load_dataset() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with aura_app.app_context():
        rows: list[dict[str, Any]] = []
        for scenario in SCENARIOS:
            car = Car.query.filter_by(vin=scenario.vin).first()
            if car is None:
                continue
            owner_email = f"wave25.owner{scenario.owner_number}@synthetic.ajebofix.invalid"
            owner = User.query.filter_by(email=owner_email).first()
            ownership = CarOwnership.query.filter_by(
                car_id=car.id,
                user_id=owner.id if owner else -1,
                is_active=True,
            ).first()
            concern = CarFault.query.filter_by(
                car_id=car.id,
                title=f"[SYNTHETIC] {scenario.scenario_id}",
                source=SYNTHETIC_SOURCE,
            ).first()
            if concern is None:
                continue
            events = (
                VehicleEvent.query.filter_by(
                    subject_type="reported_concern",
                    subject_id=concern.id,
                    source=SYNTHETIC_SOURCE,
                )
                .order_by(VehicleEvent.occurred_at.asc(), VehicleEvent.id.asc())
                .all()
            )
            classification, followup_days = _classification(scenario)
            rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "owner": owner.name if owner else "Synthetic owner unavailable",
                    "vehicle": f"{car.brand} {car.model} {car.year}",
                    "vin": car.vin,
                    "plate": ownership.plate_number if ownership else scenario.plate,
                    "category": concern.category,
                    "reported_at": _fmt(scenario.reported_at),
                    "resolved_at": _fmt(scenario.resolved_at),
                    "reopened_at": _fmt(scenario.reopened_at),
                    "followup_days": followup_days,
                    "classification": classification,
                    "concern_status": concern.status,
                    "mileage": car.current_mileage,
                    "events": [
                        {
                            "event_type": event.event_type,
                            "occurred_at": _fmt(event.occurred_at),
                            "previous_state": event.previous_state,
                            "new_state": event.new_state,
                            "progression_direction": event.progression_direction,
                            "source": event.source,
                        }
                        for event in events
                    ],
                }
            )

        with open_read_only_connection(db.engine) as connection:
            validation = build_synthetic_validation_result(connection, as_of=AS_OF)

    return rows, validation


@viewer_app.get("/")
def index():
    scenarios, validation = _load_dataset()
    return render_template_string(
        PAGE_TEMPLATE,
        scenarios=scenarios,
        validation=validation,
        dataset=SYNTHETIC_DATASET,
        source_marker=SYNTHETIC_SOURCE,
        as_of=AS_OF.strftime("%d %b %Y"),
    )


@viewer_app.get("/validation.json")
def validation_json():
    _, validation = _load_dataset()
    return jsonify(validation)


@viewer_app.get("/dataset.json")
def dataset_json():
    scenarios, validation = _load_dataset()
    payload = {
        "synthetic_only": True,
        "production_readiness_claim": False,
        "dataset": SYNTHETIC_DATASET,
        "source_marker": SYNTHETIC_SOURCE,
        "as_of": AS_OF.replace(tzinfo=timezone.utc).isoformat(),
        "validation_status": validation["status"],
        "scenarios": scenarios,
    }
    return Response(json.dumps(payload, indent=2), mimetype="application/json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    viewer_app.run(host="0.0.0.0", port=port)
