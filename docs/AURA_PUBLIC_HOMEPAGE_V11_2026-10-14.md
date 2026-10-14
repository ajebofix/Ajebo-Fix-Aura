# Aura Public Homepage V1.1 — Capability Demonstration

Status: implementation branch for review; no additional schema or account changes.

## Product language and boundaries
- Hero and feature explanations are grounded in the September 19 as-built reconciliation: vehicle memory, vehicle-scoped concerns, mileage with provenance, consultations/assessments, advisor-governed actions and Rina context.
- The product preview is **a hand-built illustration using fictional records**. It is not an authenticated screenshot or a live integration. Both preview and Rina example visibly disclose that distinction.
- No automatic diagnosis, live vehicle location, continuous telematics, predictive reliability claim, guaranteed service savings or autonomous repair decisions.
- No real client names, VINs, plates, exact odometer readings or private conversations are published.
- Rina example explicitly retains advisor authority, uncertainty and no causal conclusions.

## Changes
- Mobile-friendly tabbed example workspace: overview, vehicle memory, advisor review (keyboard-operable).
- Before/after continuity scenario with appropriate assessment caveat.
- Capability cards focusing on record continuity, health awareness, advisor workflow and Rina.
- Rina illustrated conversation with source context and explicit human oversight.
- Three transparent enquiry pathways: private access; assessment from ₦50,000; fleet/partnership. The existing POST remains canonical; the GET paths only preselect the form.
- Existing CSRF, rate limits, privacy consent, persistence, admin queue, notifications and onboarding gate unchanged.

## Release checks
Run public homepage browser CI at 375/768/1440px, full Security CI, Jinja route checks and PostgreSQL contract suites. Verify both form selection and valid submitted request without creating client accounts. Review screenshot artifacts. Confirm current Railway production SHA before merge; check healthy /healthz and /version afterward. Preserve staged unrelated Railway changes.

## Outstanding
A true redacted screenshot from an authorised demo tenant can be considered later with approval, carefully stripping any customer and vehicle identifiers. Do not misrepresent the illustrative UI as a production screenshot.
