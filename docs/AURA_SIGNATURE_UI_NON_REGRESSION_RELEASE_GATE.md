# Aura Signature UI — Non-Regression Release Gate

**Status:** Required review checklist for UI phases 1–3. Draft PRs remain unmerged until verification is complete.

## Product rule

An Aura interface change must never silently remove, disable, conceal, change permission on, or break an existing feature, action, benefit, record, workflow, or accessible control. Improvements to visual simplicity must be **progressive disclosure**, not feature deletion. A role-gated feature must stay available only to the authorized role, with its existing server-side enforcement.

**Release is blocked** whenever a user-visible regression is known, the required UI checks are failing, or a critical workflow has not been checked.

## What automated checks now protect

- Every Jinja template compiles and literal `url_for` endpoints resolve to registered Flask routes (`test_ui_control_contracts.py`).
- The Signature visual layers are loaded, key responsive states exist, and the source still contains core vehicle/dashboard/Rina actions (`test_signature_ui_contracts.py`).
- A protected baseline of essential links, forms, buttons, authority checks, and UI controls across navigation, dashboard, vehicle record, Rina chat and billing entrypoints (`test_signature_ui_preservation.py`).
- A real, signed-in owner vehicle record GET with ephemeral database records and expected controls; existing owner/driver dashboard and authority product surface tests.
- Existing feature-level suites remain necessary: Rina authority and chat cutover, client billing and delivery gates, owner/driver assignment, treatment actions, evidence/historical ingestion, security and PostgreSQL checks.

**Limits:** Presence in source and successful rendering do not prove every click, POST, touch interaction or keyboard path is correct. Only tests that actually exercise a workflow count as functional verification. CI passing is necessary but **not sufficient**.

## Manual device-and-role acceptance matrix

Run on a staging instance/test database, never by changing real client invoices or live car records to test UI.

| Role | Essential user journeys (must work before release) |
|---|---|
| Owner | Sign-in/out, garage with multiple and zero vehicles, select vehicle, full record, recorded health/risks, issue reporting, care history, assessments/PDF, consultations, treatment updates, repair journey, eligible billing and invoice/receipt preview, Rina selection/send/history, profile/security |
| Driver | Assigned vehicle only, vehicle view, routine check-in/observation actions, driver dashboard, scoped Rina and chat history, denied owner billing/approval |
| Advisor | Advisor overview, client/vehicle search, authorization, care records, recorded concerns, diagnostic codes, consultation and assessment tools, historical document review, Rina historical copilot, correction/review, appropriately scoped billing |
| Administrator | Advisor console, clients/onboarding, service records, treatment decisions, alerts, Gulf pilot when enabled, account/billing review; explicit approval controls remain intact |
| Unauthenticated / denied | Sign-in and recovery, no unauthorized private data or newly visible restricted buttons, safe failures and informative recovery when access is revoked |

### Accessibility and mobile

- Viewports: 320, 375, 390, 768, 1024 and 1440 px; iOS Safari and a desktop browser.
- No invisible, clipped, covered, untappable or offscreen buttons. Check sticky headers, side menu, tables, dialogs, select menus, PDF links, keyboard opening and safe-area insets.
- Keyboard traversal, Escape/close, focus visibility, touch targets, VoiceOver labels/announcements, long messages and long VINs.
- Reduced-motion preference and slow connection; show honest processing/empty/error states. Rina's glow is only an indicator of a real pending request.
- Test loading, failures, cancellation, retry, empty state, success, denied state, and record with no mileage. No fake telemetry or simulated care status.

## Zero-regression change management

1. **Inventory:** Compare source and authenticated visible actions against the previous production release, including conditional controls and their eligibility rules.
2. **Run:** All required source, route, authority, security, billing, Rina, and PostgreSQL regression suites; review checks **on the final exact commit**, not an earlier passing commit.
3. **Rehearse:** Complete the matrix above using staged/test accounts. Record a pass/fail and evidence for every critical journey.
4. **Approve:** UI designer/product owner signs off on both aesthetic direction and complete functional parity.
5. **Release:** Merge in order with release-ready CI and manual evidence. Monitor application errors and representative key journeys after deployment.
6. **Rollback:** Preserve last healthy release/commit and roll back immediately if a critical user journey or authorization policy breaks. Follow the incident process for any database/data integrity issue; front-end rollback alone may not reverse data changes.

## Current review status

- Phase 1: `design/aura-signature-ui-phase-1`, PR #339 (draft).
- Phase 2: `design/aura-signature-ui-vehicle-record`, PR #340 (draft, based on phase 1).
- Phase 3: `design/aura-signature-rina-presence`, PR #341 (draft, based on phase 2).
- Phases are stacked; each depends on the earlier phase and must be checked against its intended base.
- No production release is authorized by passing static/CI tests alone. The signed-in owner/advisor/driver and mobile acceptance matrix remains mandatory.
