# Rina Historical Copilot

## Purpose

Rina Historical Copilot reduces the advisor's manual historical-reconstruction workload without allowing AI to publish vehicle truth autonomously.

## Operating model

1. Rina reads the governed historical evidence already available for one explicitly selected vehicle.
2. Rina surfaces pending sources, unreviewed structured source candidates, unresolved episode attribution, likely separate jobs, unassigned context and possible identity evidence for another vehicle.
3. Rina prepares and explains candidate records using the existing historical-analysis and reconciliation pipelines.
4. The advisor reviews, edits and explicitly authorizes durable changes.
5. Once a reconciliation has been advisor-reviewed, the Advisor Workspace may execute the existing governed apply path on the advisor's explicit confirmation.

Rina never approves her own proposal.

## Missing or second vehicle

Evidence classified as `other_episode` or `unassigned` remains candidate-only. If an unresolved evidence group has the `identity` role, Rina may flag that the corpus could involve another vehicle. It must not create or assign a vehicle identity from that evidence alone. The advisor must confirm identifiers such as VIN, registration/plate and make/model/year before using the client vehicle onboarding workflow. Historical evidence remains linked to its original selected-vehicle source until a governed future reassignment workflow exists; the copilot must not silently move evidence across vehicles.

## Visibility

Historical Copilot is advisor/admin only and remains explicitly vehicle-scoped. Owner and driver sessions do not receive the advisor backlog. Rina can read candidate-only analysis that Aura has already extracted from an imported source even before the advisor has published those candidates into durable history. Evidence that has never been imported into Aura is outside Rina's visibility.

## Voice

The web client prefers a calm female English system voice when the browser exposes one. The preference list is deterministic, but the exact installed voice still depends on the user's operating system/browser. If no preferred voice is available, Aura falls back to an English system voice rather than breaking chat.

## Durable truth boundary

`historical_copilot` is read-only and candidate-only. Durable history remains governed by the existing review/reconciliation/apply workflows. Completed, authorised, recommended and unverified states must remain distinct.
