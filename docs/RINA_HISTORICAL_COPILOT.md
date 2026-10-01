# Rina Historical Copilot

## Purpose

Rina Historical Copilot reduces the advisor's manual historical-reconstruction workload without allowing AI to publish vehicle truth autonomously.

## Operating model

1. Rina reads the governed historical evidence already available for one explicitly selected vehicle.
2. Rina surfaces pending sources, unresolved episode attribution, likely separate jobs, unassigned context and possible identity evidence for another vehicle.
3. Rina prepares and explains candidate records using the existing historical-analysis and reconciliation pipelines.
4. The advisor reviews, edits and explicitly authorizes durable changes.
5. Only the existing governed apply paths may write durable vehicle history.

Rina never approves her own proposal.

## Missing or second vehicle

Evidence classified as `other_episode` or `unassigned` remains candidate-only. If an unresolved evidence group has the `identity` role, Rina may flag that the corpus could involve another vehicle. It must not create or assign a vehicle identity from that evidence alone. The advisor must confirm identifiers such as VIN, registration/plate and make/model/year before using the client vehicle onboarding workflow.

## Visibility

Historical Copilot is advisor/admin only and remains explicitly vehicle-scoped. Owner and driver sessions do not receive the advisor backlog.

## Voice

The web client prefers a calm female English system voice when the browser exposes one. The preference list is deterministic, but the exact installed voice still depends on the user's operating system/browser. If no preferred voice is available, Aura falls back to an English system voice rather than breaking chat.

## Durable truth boundary

`historical_copilot` is read-only and candidate-only. Durable history remains governed by the existing review/reconciliation/apply workflows. Completed, authorised, recommended and unverified states must remain distinct.
