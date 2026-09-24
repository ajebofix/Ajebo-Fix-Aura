# Rina advisor workspace and verified identity

The previous professional dashboard displayed an empty Rina vehicle selector, while chat required a selected vehicle. Provider context carried effective authority but omitted the signed-in display name, and the administrator/advisor wording did not explain the existing Advisor Console access model.

## Behaviour

- Ask Rina is available from navigation, the Advisor Console and the dashboard. Vehicle pages link directly to their Rina panel.
- `/chat/workspace` offers explicit, bounded search for professional accounts. Administrators retain existing access; dedicated advisor accounts see only vehicles linked by existing consultation, assessment, treatment or advisor-note records. Opening a vehicle rechecks authority.
- `/chat/account` provides deterministic account identity and basic navigation help. It never restores the selected vehicle, reads vehicle/chat records, calls a model, or saves account-help text into a vehicle history. Requests have metadata-only audit events and the same rate limits as chat. General open-ended AI chat is not part of this endpoint.
- Vehicle identity questions are answered after vehicle authorization, using the saved display name, actual account role and recorded relationships. They are audited and persisted through the existing scoped chat transaction.
- The language provider receives a compact speaker object, separate from owner data. Contact details are excluded. Instructions distinguish administrator access to the Advisor Console from ownership and a recorded advisor relationship. Chat text cannot change these facts.
- Client and advisor chat histories remain separated by user and vehicle. Vehicle changes lock the composer during binding/history loading; account mode clears the visible vehicle conversation.
- Manual and decoded vehicle labels display the year once.

## Validation

91 targeted Python tests passed across the new workspace regressions, existing Rina authority/chat/provider/memory/Advisor 360 suites, assisted onboarding, rate limits and security foundation. A real Chromium run with synthetic accounts verified the mobile console entry, account identity without a vehicle, client-name search, vehicle identity, account-mode isolation from a stale vehicle binding, vehicle switching and desktop rendering. No page JavaScript errors were observed. No live client records were used or modified.

## Rollout

No schema migration or new environment variables. `RINA_ADVISOR_360_ENABLED` remains unchanged and OFF by default. This change does not enable the longitudinal-context expansion. Deployment health and the signed-in user journey should be checked after release. Updated routes are generated in `AURA_REGISTERED_ROUTES_2026-09-24.md`.
