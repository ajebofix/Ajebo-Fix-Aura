# Aura UI/UX and Front-End Audit — 2026-10-09

**Scope:** Aura by Ajebo Fix, Railway-hosted Flask/Jinja front end. This is a *source-wide first-pass audit* and regression-hardening wave. It is not a claim that every behavioural or visual bug is found. The separate Vercel Billing application is not modified.

## Coverage and methodology

- Enumerated the production GitHub repository tree (706 entries) and inspected the Aura template groups: base/navigation; authentication; dashboards; client vehicles; admin/consultation/alerts; drivers; Billing; evidence; historical ingestion; reports; maintenance; treatment plans; mileage; profile; Rina chat.
- Inspected shared CSS imports: theme, layout, mobile, form, button, sidebar, component and animation styles.
- Reviewed source action markup, forms, inline click handlers, static `url_for` references, and administrator/customer separation.
- Added regression checks that **compile all Jinja page templates and verify that every literal `url_for` target exists in the Flask route registry**. This is a structural link check, not a claim that every click was exercised in the browser.
- Completed a live unauthenticated mobile login browser check (25 steps). The navigation opens and closes; password field visibility changes, but the Show/Hide label did not update. The menu covering the underlying form while open is expected for a drawer, not by itself a defect. This check does not grant access to private client or admin pages.
- Existing Billing invariants are preserved: financial records originate in Billing; publication and email are separately approved; users retain the original manual editor; never record payments by clicking a front-end action.

## Reproducible defects addressed

| ID | Severity | Area | Evidence/root cause | Fix | Verification |
|---|---|---|---|---|---|
| UI-001 | High | Shared responsive layout | `static/css/mobile.css` declared mobile `.app-shell{display:block!important}`, fixed sidebar and mobile toggle **without a breakpoint**. Desktop grid was overridden globally. | Restrict all mobile-only layout changes to `@media(max-width:900px)`, restore desktop menu visibility and prevent horizontal clipping. | Source contract + browser/device follow-up |
| UI-002 | High | Hidden-looking buttons | Templates use `.btn-secondary`, `.btn-navy` and `.btn-ghost`, but global `buttons.css` did not define these variants. White-on-white or borderless controls could appear absent. | Add high-contrast secondary, navy, gold, ghost and outline styles; visible focus rings, disabled styling and action stacks. | CSS regression contract |
| UI-003 | High | Form controls | Global `forms.css` gave **every** input, including checkboxes/radios, `width:100%`, padding and large margins. | Correct text-input selectors and give choice controls fixed 18px sizing and focus indications. | CSS regression contract |
| UI-004 | Medium | Mobile navigation | Menu could not be dismissed with Escape, overlay was not a semantic button, menu did not indicate the current page, and background remained scrollable. | Add semantic close surface, Escape handler, focus behaviour, `aria-expanded`, `aria-current` and mobile body-scroll containment. | Static contract; mobile interaction follow-up |
| UI-005 | Medium | Dashboard vehicle selection | `selectVehicle` reloaded on every HTTP response, including permission errors; users saw no explanation and the action looked dead. | Check response status and JSON, keep button disabled only while working, present an in-page error and restore it on failure. | Source contract; permission-based integration follow-up |
| UI-006 | Medium | Login and signup | Show-password buttons toggled the input but continued displaying **Show** even after revealing the password. | Reflect Show/Hide state and `aria-pressed`, name and controls. | Source and live public browser |
| UI-007 | Medium | Password change | Password page used Bootstrap-only classes without Bootstrap being loaded, leaked an extra document doctype and accepted `minlength=8` while the server requires 10. | Replace with native Aura card/form, explicit links, proper labels, show-password control and 10-character rule. | Compiled template + policy contract |
| UI-008 | Medium | Password reset | Form enforced eight characters but backend required at least ten. | Align client validation and guidance with the backend. | Static policy contract |
| UI-009 | Medium | Activation links | Copy action relied exclusively on the Clipboard API, common in-app browser permission failure left no response. | Add select/copy fallback and visible guidance if clipboard is denied. | Source; in-app iOS manual follow-up |
| UI-010 | Low | Sidebar branding | Invalid CSS values `12x` and `2-px` silently discarded shadow styles. | Correct CSS units. | Source inspection |
| UI-011 | Medium | Admin concern status | Two admin concern actions were native unstyled buttons with no `.btn` classes or explicit type. | Apply visible primary/secondary styles and explicit submit type. | Source; administrator browser follow-up |
| UI-012 | Preventive | Cross-app links | No unified check of hardcoded Jinja route names across all page templates. | New `test_ui_control_contracts.py` and `ui-ux-contract-ci.yml` for all Jinja compile/link checks and shared styling invariants. | Automated CI |
| UI-013 | High | Finalized vehicle health PDF | The health screen linked to `car_assessments.client_download_assessment_pdf`, but that blueprint endpoint is not registered in production. The Download Report button would fail with a Flask URL build error. | Point to the registered, owner-safe `assessment_reports.assessment_report_pdf` endpoint. | Template route test caught this, recheck CI after fix. |

## Still open / needs authenticated or device testing

1. Run actual signed-in owner/admin/driver browser sessions on iPhone Safari and desktop for every navigation entry, button, form, error state, validation and confirmation. A source reference to a registered endpoint does **not** prove that the endpoint works in every runtime state.
2. Exercise Rina's 64KB chat component across offline, loading, send, historical copilot, context clearing, search and permission states. Some deliberately hidden controls are conditional, not necessarily defects.
3. Validate financial flows using **nonfinancial test records**: Draft→Review & Issue→Review & Publish→Review & Send; partially paid invoice→payment-backed receipt; no duplicate send. No production financial writes for test purposes.
4. Check screenshots at 320px, 375px, 390px, 768px and 1440px for focus visibility, overflow, cards, tables, buttons, and iOS safe areas.
5. Inspect all conditional or admin-gated actions while authenticated with each role; do not bypass permission boundaries to make a button appear.
6. Consider screenshot-based Playwright smoke checks and automated axe accessibility checks in the next pass. Current Jinja/route/static checks do not cover pixel appearance or all JavaScript interactions.
7. Ajebo Fix Billing (separate Vercel app) requires its own UI audit: this wave audits Aura and its Billing interface *inside Aura* only.

## Release process and rollback

- New regression suite runs against the full app route registry and template catalog in PR CI.
- Before merging, confirm regression checks plus existing Aura Security, Billing and PostgreSQL tests. No migration/data writes are part of this UI patch.
- After Railway deploy reports SUCCESS, check `https://aura.ajebofix.com/healthz` is serving the merge commit and verify public login mobile and the private flows above.
- Roll back via Railway to the preceding healthy deployment if critical navigation, authentication or owner visibility fails.

## Operating rule

**Automation complements manual control.** Users must always be able to take deliberate supported actions. Hidden items that are gated by role, document status or explicit approval must remain gated and should explain their unavailable state rather than masquerading as dead controls.
