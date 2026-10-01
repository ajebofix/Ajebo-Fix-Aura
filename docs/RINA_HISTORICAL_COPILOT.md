# Rina Historical Copilot

## Purpose

Rina Historical Copilot reduces the advisor's manual historical-reconstruction workload without allowing AI to publish vehicle truth autonomously.

Historical Intelligence v2 is the pilot reconstruction layer behind the Copilot. It is designed for messy longitudinal sources such as WhatsApp exports that may contain more than one vehicle, many service episodes, mixed media and facts that are already represented elsewhere in Aura.

## Historical Intelligence v2 operating model

1. Aura materialises the imported source once. For a WhatsApp ZIP this includes the parsed conversation plus supported PDFs/documents, images, audio/voice-note transcriptions and video observations.
2. Aura records a deterministic source-coverage manifest so Rina can state how much of the archive was actually processed and which media, if any, failed or was rejected.
3. Rina performs a whole-corpus vehicle census before assuming which vehicle an item belongs to. The selected/uploaded vehicle is trusted context, not a rule that every message belongs to it.
4. Rina segments the complete chronology into distinct service-episode candidates rather than collapsing the source into one current case.
5. Aura supplies the client's known vehicle identities and durable canonical history to the structuring pass.
6. Every reconstructed episode receives a canonical comparison: `already_represented`, `partially_represented`, `missing_from_durable_history`, `conflicting`, `uncertain`, or `belongs_to_other_vehicle`.
7. Rina may describe an episode as missing only when the canonical comparison says it is missing. Existing canonical Treatment Actions are never re-proposed merely because the WhatsApp wording differs.
8. Rina prepares candidate records and explains the evidence. The advisor reviews, edits and explicitly authorizes durable changes.
9. Once a reconciliation has been advisor-reviewed, the Advisor Workspace may execute the existing governed apply path on the advisor's explicit confirmation.

Rina never approves her own proposal.

## Persistent reconstruction

The v2 vehicle census, service-episode reconstruction, canonical comparisons and source-coverage result are stored in Aura's existing encrypted extraction payload. Rina therefore reuses a persisted reconstruction instead of rediscovering the client's historical structure from scratch on every chat turn.

The advisor may explicitly run or rebuild Historical Intelligence v2 from an already-imported WhatsApp bundle. The original ZIP does not need to be uploaded again.

## Missing or second vehicle

A WhatsApp source may contain multiple vehicles. Historical Intelligence v2 therefore looks for distinct vehicle identity evidence before episode assignment. A different VIN or plate is strong evidence, but it may also preserve a possible second vehicle when the corpus repeatedly refers to a different make/model/year, a separate vehicle in media/documents, or explicit contextual references such as another car.

Possible or uncertain vehicle identities remain proposals. Rina must not create or assign a new vehicle from them autonomously. The advisor confirms identity before using the client vehicle onboarding workflow. Historical evidence remains linked to its original imported source until a governed reassignment workflow exists; the Copilot must not silently move evidence across vehicles.

## Canonical diff

The durable Aura record has precedence over historical source wording. The canonical comparison is performed against supplied Treatment Actions, historical service episodes and vehicle events.

- `already_represented`: the meaningful source-supported episode is already preserved in Aura.
- `partially_represented`: Aura already contains some of it; only explicitly listed missing facts may be proposed.
- `missing_from_durable_history`: no semantically equivalent supplied canonical record represents the source-supported episode.
- `conflicting`: source evidence and canonical history disagree materially.
- `uncertain`: evidence is insufficient for a safe classification.
- `belongs_to_other_vehicle`: the episode should not be attached to the selected vehicle.

A source may add provenance or context to an existing canonical action without becoming a duplicate historical job.

## Source coverage contract

Historical Intelligence v2 must not claim that it reviewed the complete WhatsApp export merely because the parent ZIP was imported.

Coverage records the parsed WhatsApp-message count, archive-member count, supported materialised items, completed extraction types, failed media analysis, rejected unsafe items and unsupported/skipped archive members. Rina may call the supported evidence fully reviewed only when the coverage result says it is complete. Partial coverage must be disclosed to the advisor.

## Visibility and authority

Historical Copilot is advisor/admin only and remains explicitly vehicle-scoped at the workspace boundary. Owner and driver sessions do not receive the advisor backlog.

Rina can read candidate-only analysis that Aura has already extracted from an imported source before the advisor publishes those candidates into durable history. Evidence that has never been imported into Aura is outside Rina's visibility.

`historical_copilot` is candidate/read-only context. Durable history remains governed by the existing review/reconciliation/apply workflows. Completed, authorised, recommended and unverified states remain distinct.

## Voice

The web client prefers a calm female English system voice when the browser exposes one. The preference list is deterministic, but the exact installed voice still depends on the user's operating system/browser. If no preferred voice is available, Aura falls back to an English system voice rather than breaking chat.
