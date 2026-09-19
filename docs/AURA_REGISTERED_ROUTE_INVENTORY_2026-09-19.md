# Aura Registered Route Inventory — 19 September 2026

**Scope:** routes registered by the current Flask application factory and route/cutover modules on `main`  
**Base observed during reconciliation:** `1ece91f5e12a29962dedfe6bd9a7a93315ba1b35`

## 1. Reading this inventory

This is the **as-built route catalogue**, not the historical route proposal in `scripts/document structure/route_map.md`.

Rules for this snapshot:

- implicit Flask `HEAD` / `OPTIONS` methods are omitted;
- the built-in static route is omitted;
- routes are shown with the effective blueprint prefix;
- some legacy URLs remain registered for compatibility while `record_once` cutover modules replace the current view function behind the same endpoint;
- a URL's physical decorator file therefore does not always identify the final lifecycle service that handles the request;
- `/assessments/.../report*` entries are direct `add_url_rule` registrations from the assessment module.

## 2. Application / runtime

| Methods | Route | Purpose |
|---|---|---|
| GET | `/` | service identity/status |
| GET, POST | `/login` | compatibility alias to `/auth/login` |
| GET | `/version` | runtime commit/environment/database identity |
| GET | `/healthz` | readiness/schema/evidence configuration check |

## 3. Authentication, verification and sessions

| Methods | Route |
|---|---|
| GET, POST | `/auth/signup` |
| GET, POST | `/auth/login` |
| POST | `/auth/logout` |
| GET, POST | `/auth/forgot-password` |
| GET, POST | `/auth/change-password` |
| GET, POST | `/auth/reset-password/<token>` |
| GET, POST | `/auth/activate/<token>` |
| GET, POST | `/account/setup` |
| GET | `/auth/verify-email` |
| GET | `/auth/verification-required` |
| POST | `/auth/resend-verification` |
| GET | `/auth/sessions` |
| POST | `/auth/sessions/<int:session_id>/revoke` |
| POST | `/auth/sessions/revoke-others` |

### Compatibility overlap

Two endpoints currently register `GET /admin/dashboard` through separate blueprints:

- `advisor.admin_dashboard` from `auth/routes.py`;
- `admin.admin_dashboard` from `admin/routes.py`.

This is compatibility-sensitive route debt and should not be expanded.

## 4. Client vehicle routes

| Methods | Route |
|---|---|
| GET | `/cars/` |
| GET, POST | `/cars/add` |
| GET | `/cars/<int:car_id>` |
| GET | `/cars/<int:car_id>/health` |
| GET, POST | `/cars/<int:ownership_id>/service/add` |
| GET, POST | `/cars/<int:car_id>/concerns/add` |
| GET | `/cars/<int:car_id>/report` |
| GET | `/cars/<int:car_id>/records` |
| GET | `/cars/<int:car_id>/records/pdf` |
| GET, POST | `/cars/<int:car_id>/consultations/book` |
| GET | `/cars/<int:car_id>/assessment/report` |
| GET, POST | `/cars/<int:car_id>/priority-request` |
| POST | `/cars/<int:car_id>/emergency-review` |
| GET | `/cars/<int:car_id>/priority-status` |
| POST | `/cars/<int:car_id>/priority-requests/<int:request_id>/cancel` |
| GET | `/cars/treatment-plans` |
| POST | `/cars/treatment-plans/<int:plan_id>/authorize` |
| GET | `/cars/<int:car_id>/maintenance` |
| GET | `/cars/<int:car_id>/faults` |
| GET, POST | `/cars/<int:car_id>/faults/add` |
| GET | `/cars/debug/run-reminders` |

The `/faults` URL family remains intentionally stable while the product concept is **Reported Concern**.

## 5. Dashboard and client profile

| Methods | Route |
|---|---|
| GET | `/dashboard/` |
| POST | `/dashboard/select-vehicle` |
| GET | `/profile/` |
| GET, POST | `/profile/edit` |
| GET | `/profile/privacy` |

## 6. Driver routes

| Methods | Route |
|---|---|
| GET | `/driver/dashboard` |
| GET | `/driver/cars/<int:car_id>` |
| POST | `/driver/cars/<int:car_id>/report` |
| GET, POST | `/driver/cars/<int:car_id>/check-in` |

## 7. Mileage routes

| Methods | Route |
|---|---|
| GET, POST | `/mileage/cars/<int:car_id>/report` |
| GET, POST | `/admin/cars/<int:car_id>/odometer` |
| POST | `/admin/cars/<int:car_id>/odometer-reports/<int:observation_id>/accept` |
| POST | `/admin/cars/<int:car_id>/odometer-reports/<int:observation_id>/reject` |

## 8. Rina / chat

| Methods | Route |
|---|---|
| GET | `/chat/context` |
| POST | `/chat/select-vehicle` |
| POST | `/chat` |
| GET | `/chat/history` |
| GET | `/admin/rina/provider-status` |

## 9. Client and advisor health surfaces

| Methods | Route |
|---|---|
| GET | `/health/cars/<int:car_id>/health/records` |
| GET | `/health/advisor/cars/<int:car_id>/health/records` |
| GET | `/clinical_notices/cars/<int:car_id>/health/notices` |
| GET | `/clinical_notices/advisor/health/notices` |
| GET | `/health_trajectory/cars/<int:car_id>/health/trajectory` |
| GET | `/intelligence/cars/<int:car_id>/health` |
| GET | `/intelligence/cars/<int:car_id>/rina/insight` |

The advisor-wide Alert Center is the operational surface for durable care signals and read-time projections; health/notice compatibility routes should not be allowed to form a parallel workflow.

## 10. Stewardship

| Methods | Route |
|---|---|
| GET | `/stewardship/cars/<int:car_id>/stewardship` |
| GET | `/stewardship/cars/<int:car_id>/stewardship/history` |
| POST | `/stewardship/cars/<int:car_id>/stewardship/transfer` |
| POST | `/stewardship/advisor/cars/<int:car_id>/stewardship/reassign` |

## 11. Treatment-record audit API

| Methods | Route |
|---|---|
| POST | `/treatments/cars/<int:car_id>/records` |
| GET | `/treatments/cars/<int:car_id>/records` |
| PATCH | `/treatments/records/<int:record_id>` |
| DELETE | `/treatments/records/<int:record_id>` |
| GET | `/audit/records/<int:event_id>/history` |
| GET | `/audit/advisor/records/<int:event_id>/history` |

## 12. Evidence

### Intake and private retrieval

| Methods | Route |
|---|---|
| GET | `/evidence/vehicles/<int:car_id>/submit` |
| POST | `/evidence/vehicles/<int:car_id>/images` |
| POST | `/evidence/<int:evidence_id>/grant` |
| POST | `/evidence/<int:evidence_id>/content` |
| POST | `/evidence/<int:evidence_id>/delete` |
| GET | `/evidence/vehicles/<int:car_id>/timeline` |

### Advisor evidence

| Methods | Route |
|---|---|
| GET | `/admin/evidence/vehicles/<int:car_id>/pending` |
| GET | `/admin/evidence/<int:evidence_id>/workspace` |
| POST | `/admin/evidence/<int:evidence_id>/review` |
| POST | `/admin/evidence/<int:evidence_id>/links/reported-concerns/<int:concern_id>` |
| GET | `/admin/evidence/vehicles/<int:car_id>/timeline` |

## 13. Advisor console — core

| Methods | Route |
|---|---|
| GET | `/admin/dashboard` |
| GET | `/admin/clients` |
| GET, POST | `/admin/clients/new` |
| GET | `/admin/clients/<int:user_id>` |
| POST | `/admin/clients/<int:user_id>/activation-link` |
| GET, POST | `/admin/clients/<int:user_id>/vehicles/new` |
| POST | `/admin/clients/<int:user_id>/notes/add` |
| GET | `/admin/fleet/health` |
| GET | `/admin/search` |
| GET | `/admin/control` |

## 14. Advisor console — Reported Concerns

| Methods | Route |
|---|---|
| GET | `/admin/concerns` |
| GET | `/admin/faults` |
| GET | `/admin/cars/<int:car_id>/concerns` |
| GET, POST | `/admin/cars/<int:car_id>/concerns/add` |
| POST | `/admin/concerns/<int:concern_id>/review` |
| POST | `/admin/concerns/<int:concern_id>/monitor` |
| POST | `/admin/concerns/<int:concern_id>/resolve` |
| GET | `/admin/concerns/<int:concern_id>/progression` |

## 15. Advisor console — vehicle records and service history

| Methods | Route |
|---|---|
| GET | `/admin/cars/<int:car_id>` |
| GET, POST | `/admin/cars/<int:car_id>/service/add` |
| GET | `/admin/cars/<int:car_id>/records` |
| GET | `/admin/cars/<int:car_id>/records/pdf` |
| GET | `/admin/cars/<int:car_id>/historical-service-verification` |
| POST | `/admin/cars/<int:car_id>/service-events/<int:event_id>/verification-review` |
| POST | `/admin/cars/<int:car_id>/service-events/<int:event_id>/maintenance-classification` |
| GET | `/admin/cars/<int:car_id>/maintenance` |

## 16. Advisor console — VIN / DTC

| Methods | Route |
|---|---|
| POST | `/admin/vehicles/<int:car_id>/decode-vin` |
| POST | `/admin/vehicles/<int:car_id>/dtcs/add` |
| POST | `/admin/vehicles/<int:car_id>/dtcs/<int:dtc_id>/clear` |

## 17. Consultation lifecycle

| Methods | Route |
|---|---|
| GET, POST | `/admin/cars/<int:car_id>/consultations/schedule` |
| GET | `/admin/consultations` |
| POST | `/admin/consultations/<int:consultation_id>/start` |
| GET, POST | `/admin/consultations/<int:consultation_id>/complete` |
| GET, POST | `/admin/consultations/<int:consultation_id>/schedule-request` |

The registration URLs above remain stable while `services/consultation_route_cutover.py` replaces lifecycle handlers at blueprint registration time.

## 18. Assessment lifecycle

| Methods | Route |
|---|---|
| POST | `/admin/consultations/<int:consultation_id>/assessment/start` |
| GET, POST | `/admin/assessments/<int:assessment_id>/edit` |
| POST | `/admin/assessments/<int:assessment_id>/finalize` |
| GET, POST | `/admin/assessments/<int:assessment_id>/addenda` |
| GET | `/admin/assessments/<int:assessment_id>/download` |
| GET | `/assessments/<int:assessment_id>/report` |
| GET | `/assessments/<int:assessment_id>/report.pdf` |

The two `/assessments/.../report*` routes are installed directly with `app.add_url_rule` for stable report endpoints.

Assessment start/edit/finalize behavior is compatibility-sensitive and uses lifecycle/correction cutover services.

## 19. Treatment Plan / Treatment Action lifecycle

| Methods | Route |
|---|---|
| POST | `/admin/treatment-plans/<int:plan_id>/start` |
| POST | `/admin/treatment-plans/<int:plan_id>/complete` |
| POST | `/admin/treatment-plans/<int:plan_id>/defer` |
| GET | `/admin/treatment-actions` |
| GET | `/admin/treatment-plans/<int:plan_id>/actions` |
| POST | `/admin/treatment-plans/<int:plan_id>/schedule` |
| POST | `/admin/treatment-plans/<int:plan_id>/actions` |
| POST | `/admin/treatment-actions/<int:action_id>/schedule` |
| POST | `/admin/treatment-actions/<int:action_id>/<operation>` |
| POST | `/admin/treatment-actions/<int:action_id>/evidence` |
| POST | `/admin/treatment-plans/<int:plan_id>/outcomes` |

Treatment Plan legacy state URLs are preserved, while the current lifecycle layer owns valid transitions and canonical event emission.

## 20. Priority Access

| Methods | Route |
|---|---|
| POST | `/admin/cars/<int:car_id>/priority-access` |
| POST | `/admin/cars/<int:car_id>/care-pathway` |
| GET | `/admin/priority-requests` |
| POST | `/admin/cars/<int:car_id>/priority-request` |
| POST | `/admin/priority-requests/<int:request_id>/review` |
| POST | `/admin/priority-requests/<int:request_id>/accept` |
| POST | `/admin/priority-requests/<int:request_id>/defer` |
| POST | `/admin/priority-requests/<int:request_id>/resolve` |
| POST | `/admin/priority-requests/<int:request_id>/cancel` |
| GET, POST | `/admin/priority-requests/<int:request_id>/consultation` |

The durable workflow is `PriorityRequest`. Advisor review scoring and care-plan entitlement are projections/context, not substitutes for this lifecycle.

## 21. Alert Center / care signals

| Methods | Route |
|---|---|
| GET | `/admin/alerts` |
| GET | `/admin/alerts/<int:alert_id>/history` |
| POST | `/admin/alerts/<int:alert_id>/acknowledge` |
| POST | `/admin/alerts/<int:alert_id>/resolve` |

Care-signal lifecycle handlers are governed through the cutover layer so the durable `VehicleHealthAlert` state and canonical event history stay synchronized.

## 22. Driver management

| Methods | Route |
|---|---|
| POST | `/admin/cars/<int:car_id>/invite-driver` |
| POST | `/admin/drivers/remove/<int:driver_id>` |

## 23. Compatibility/cutover modules that affect registered endpoints

The following modules are imported during `admin.progression_routes` registration and intentionally change behavior without necessarily adding a new URL family:

- `priority.routes`
- `services.consultation_route_cutover`
- `services.priority_route_cutover`
- `services.assessment_route_cutover`
- `services.assessment_correction_routes`
- `services.treatment_plan_route_cutover`
- `services.treatment_action_routes`
- `services.care_signal_route_cutover`
- `services.service_history_route_cutover`
- `services.mileage_routes`
- `services.maintenance_service_routes`
- `services.historical_service_verification_routes`
- `services.maintenance_surface_routes`

This is why future refactors must inspect both the decorator location **and** the cutover installation before changing a route.

## 24. Known route debt

The current inventory confirms several architectural cleanup targets:

1. `GET /admin/dashboard` has overlapping compatibility registrations.
2. broad `admin/routes.py` still owns too many unrelated concerns.
3. legacy `/faults` naming remains for URL stability.
4. old lifecycle route functions may remain physically present even when a cutover service owns current behavior.
5. health/notice/trajectory families retain compatibility surfaces that must not evolve into competing workflows.
6. report routes are split between blueprints and direct `add_url_rule` registration.

These are maintainability issues, not justification for another wholesale rewrite.

## 25. Reproducibility rule

Whenever routes are added, removed or cut over, regenerate this inventory from the Flask application rather than updating the historical `scripts/document structure/route_map.md` by intuition.

A runtime route-snapshot helper should use:

```python
for rule in sorted(app.url_map.iter_rules(), key=lambda item: (item.rule, item.endpoint)):
    methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
    print(rule.rule, methods, rule.endpoint)
```

The actual Flask `url_map` remains the final authority if a future source-level catalogue and runtime registration ever disagree.
