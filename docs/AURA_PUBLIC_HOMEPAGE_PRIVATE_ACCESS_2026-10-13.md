# Aura public homepage & private-access launch gate

**Status:** implementation proposed in feature branch; not yet deployed.

## Routes
- GET `/`: public product homepage (replaces root JSON status; existing `/healthz` and `/version` preserved).
- POST `/request-access`: CSRF-protected, rate-limited intake for private access, assessments and fleet enquiries. Stores enquiry; sends best-effort team notification through existing Resend transactional email.
- GET `/admin/access-requests`: advisor/admin-only queue of up to 100 recent enquiries.
- POST `/admin/access-requests/<id>/status`: advisor-only CSRF-protected status update.
- `/auth/login`: unaffected; existing accounts continue to sign in.
- `/auth/signup`: when private access mode enabled, a valid driver invitation code is mandatory; owners use existing advisor-created account activation.

## Operational configuration
- `AURA_PRIVATE_ACCESS_ONLY`: defaults to **on for production** and off in development/testing for legacy workflow compatibility. Set `true` explicitly to exercise the private gate in staging. Do not bypass gate by hiding the signup navigation only.
- `AURA_ACCESS_REQUEST_NOTIFY_EMAIL`: defaults to `ajebofix@gmail.com`. Mail transport needs existing Resend configuration; delivery failure does not lose the database record.
- `RATE_LIMIT_STORAGE_URI` or `REDIS_URL`: use shared Redis for rate-limited public submissions.
- Apply the additive Alembic migration `a13f7b6c2d90` before serving requests.
- Confirm customer-facing privacy policy adequately covers enquiry retention/response and provide a deletion process before launch.

## Launch gates and testing
- Run `flask db heads`, `flask db upgrade`, `pytest tests/test_public_private_access.py tests/test_ui_control_contracts.py` in a disposable environment first; confirm migration remains linear.
- Verify driver signup with a valid unused invitation; anonymous owner POST must not mint a user when gate is enabled.
- Verify admin-only access request list/status update, CSRF, rate limiting, duplicate taps, malicious input, static asset URLs, privacy link, Resend notification, 320/390/768/1440px responsiveness.
- Confirm /healthz and /version still answer correctly and authenticated dashboards remain unchanged.
- Review current `AURA_PRIVATE_ACCESS_ONLY` value before production deploy; production defaults to protected mode.
- Do not market membership, live telematics, predictive diagnoses, warranty/TSB intelligence or guaranteed response times as currently available.
- Rollback: restore preceding code deployment; do not destroy submitted enquiries in a DB downgrade.

## Content positioning
Landing page communicates recorded history, professional oversight and human-governed Rina. Vehicle panel is explicitly marked illustrative, with no fabricated metrics, testimonials or patient data. Vehicle assessment pricing is described as starting from ₦50,000, subject to scope. No client or partner is automatically accepted.
