# Aura Wave 1.4 — Evidence Production Closeout

**Status:** Production-proven first slice  
**Scope:** Secure image evidence  
**Date:** 18 August 2026  
**Production application commit:** `a065f99107c7ae460512405a2aa7614eac9c6318`

## 1. Purpose

This document closes the first production slice of Wave 1.4: secure, advisor-governed **image evidence**.

It records what was actually exercised in production, the security and authority boundary that is now canonical, the live feature state, rollback controls, and the multimodal work that remains deliberately deferred.

This closeout does **not** redefine every item in Issue #32 as complete. PDF, audio, extraction/transcription and other deferred multimodal capabilities remain future work.

## 2. Canonical production flow

The production-proven trust boundary is:

```text
authorised owner / assigned driver
        ↓
explicit vehicle + evidence purpose + consent
        ↓
server-side validation and image sanitisation
        ↓
private Cloudflare R2 object storage
        ↓
VehicleEvidence = pending_review
        ↓
short-lived user/evidence-bound private retrieval grant
        ↓
protected content delivery through Aura
        ↓
advisor / administrator review
        ↓
accepted | rejected
        ↓
optional same-vehicle Reported Concern association
        ↓
canonical evidence.reviewed / evidence.linked events
        ↓
role-specific reviewed evidence projection
```

Media remains evidence. It does not silently become a diagnosis, a treatment decision or a Reported Concern progression event.

## 3. Production verification completed

### Private storage

- Cloudflare R2 bucket created specifically for Aura production evidence.
- Standard storage class used.
- Public access remains disabled.
- Account token is restricted to Object Read & Write for the production evidence bucket only.
- R2 credentials are stored only as Railway production variables.
- No public R2 or presigned object URL is exposed to Aura clients.

### Storage smoke test

The deliberate production smoke test was executed from the live Aura Railway container:

```text
EVIDENCE_STORAGE_SMOKE_CONFIRM=1 python scripts/smoke_evidence_storage.py
```

Result:

```text
Evidence storage smoke test passed: write, read, integrity, and delete verified.
```

The smoke path verified temporary private write, read, byte integrity, existence, deletion and confirmed post-delete absence without printing storage secrets or object identifiers.

### Image intake

A real owner-side mobile image submission was completed in production.

Production response:

```text
POST /evidence/vehicles/1/images → 201 Created
```

The client received the expected pending-professional-review receipt. Submission did not create a diagnosis.

### Private retrieval

The advisor workspace initially withheld media. The advisor explicitly selected **Securely View Image**, causing Aura to create and consume the short-lived protected retrieval grant.

The private image rendered inside the review session without exposing the underlying R2 storage URL, object key, bucket or credentials.

### Advisor review and linkage

The production advisor flow successfully:

- found the pending evidence in the vehicle queue;
- loaded the protected review workspace;
- retrieved the private image;
- recorded an accepted professional review;
- prevented the same item from subsequently being classified through the opposite review action;
- associated the accepted evidence with an existing same-vehicle Reported Concern in `Monitoring` state.

The association records supporting evidence only. It does not establish diagnosis or change Reported Concern progression by itself.

### Reviewed timeline / role visibility

Production testing confirmed differentiated projections:

**Advisor / administrator view** may show controlled governance context such as visibility classification and controlled review basis.

**Owner view** receives client-safe reviewed history and care-record association while omitting:

- R2/object-storage location;
- object keys;
- checksums/hashes;
- retrieval grants;
- reviewer IDs/internal authority mechanics;
- raw canonical event payloads;
- private advisor-only metadata.

Rejected or not-used evidence is represented as reviewed but excluded from professional supporting evidence.

## 4. Production feature state

The following production feature gates are intentionally active:

```text
EVIDENCE_IMAGE_INTAKE_ENABLED      = ON
EVIDENCE_RETRIEVAL_ENABLED         = ON
EVIDENCE_ADVISOR_REVIEW_ENABLED    = ON
EVIDENCE_TIMELINE_ENABLED          = ON
```

The following remains intentionally disabled:

```text
EVIDENCE_ADVISOR_DELETION_ENABLED  = OFF
```

Advisor deletion must not be activated casually. The underlying governed deletion/reconciliation capability exists, but production enablement requires a specific operational need and a deliberate runbook decision.

## 5. Cutover guardrails

Wave 1.4 production cutover is guarded by the configuration-only evidence readiness contract introduced in PR #64.

Production fails closed when an enabled capability is missing its required storage or policy configuration.

`/healthz` exposes credential-safe evidence readiness only. It never returns storage account IDs, bucket names or credentials.

The validated dependency order is:

```text
image intake
    ↓
private retrieval
    ↓
advisor review
    ↓
reviewed timeline
```

These feature gates remain independently reversible.

## 6. Production hardening completed

### Evidence UX

Post-cutover production testing exposed and fixed:

- owner upload entry hidden while timeline was deliberately OFF — PR #65;
- mobile consent-row overflow — PR #66;
- advisor pending-review entry hidden while timeline was deliberately OFF — PR #67;
- reviewed-record wording, submission-purpose clarity and completed-review presentation — PR #68.

The canonical `concern_support` purpose was not changed merely for presentation. The UI now uses clearer human wording while preserving the existing database contract.

### Shared rate limiting

Aura's production Flask-Limiter backend was moved from process-local `memory://` storage to a private Railway Redis service.

Production state:

- Redis service is private-network only;
- Redis requires a password;
- Redis has persistent `/data` storage;
- Aura receives `REDIS_URL` through a Railway service-reference variable rather than a copied secret;
- the previous production `memory://` fallback warning is absent from the current deployment;
- current startup/runtime logs contain no Redis or Flask-Limiter authentication/configuration exceptions.

The application already prefers `RATE_LIMIT_STORAGE_URI`, then `REDIS_URL`, then `memory://`; no parallel rate-limit implementation was introduced.

A Redis host-level `vm.overcommit_memory` advisory is visible in the managed Redis container logs. It is an infrastructure/runtime advisory, not evidence-domain behavior, and did not prevent Redis from reaching `Ready to accept connections tcp`.

## 7. Canonical architectural boundaries after Wave 1.4

Future evidence work must reuse these contracts rather than create parallel paths:

1. **Authority first** — current vehicle authority and verified identity are resolved before evidence access.
2. **Private storage only** — no direct public object URL.
3. **Server-mediated validation** — client filename/type claims are not trusted.
4. **Evidence is not diagnosis** — media does not autonomously establish a fault, cause or treatment.
5. **Human review before professional truth** — pending evidence is not trusted care-record evidence until reviewed.
6. **Visibility is explicit** — client/advisor/internal boundaries survive upload, review, events and timeline projection.
7. **Canonical events remain governed metadata** — no raw media, secrets, provider prompts or diagnosis text in event payloads.
8. **Evidence does not silently alter mechanical progression** — `evidence.reviewed` and `evidence.linked` remain `not_applicable` to progression unless a later explicit clinical rule is designed and approved.
9. **Provider abstraction remains mandatory** — storage and future extraction/AI providers sit behind Aura-owned contracts.
10. **Feature gates remain rollback controls** — production capability can be reduced without removing historical reviewed records.

## 8. Rollback strategy

If an evidence incident occurs, reduce capability in reverse dependency order:

```text
1. disable EVIDENCE_TIMELINE_ENABLED if reviewed projection itself is unsafe;
2. disable EVIDENCE_ADVISOR_REVIEW_ENABLED to prevent new professional decisions;
3. disable EVIDENCE_RETRIEVAL_ENABLED to prevent private media retrieval;
4. disable EVIDENCE_IMAGE_INTAKE_ENABLED to stop new uploads.
```

Historical database records and private objects must not be destroyed as part of ordinary rollback.

If only provider/storage access is impaired, prefer disabling the affected capability and preserving records for reconciliation rather than deleting evidence or inventing a successful state.

`EVIDENCE_ADVISOR_DELETION_ENABLED` remains OFF and is not part of the normal cutover rollback path.

## 9. Deferred Wave 1.4 / future multimodal scope

The following are **not production-complete** under this closeout:

- PDF evidence intake;
- voice-note/audio evidence intake;
- unrestricted video intake;
- OCR/document extraction;
- transcription;
- image-understanding / multimodal AI interpretation;
- malware-scanning expansion for document/audio formats;
- client-facing deletion rights/workflow;
- production activation of advisor-governed deletion;
- Rina multimodal reasoning or automated diagnostic inference from media.

These must extend the existing `VehicleEvidence` authority, storage, provenance, review, event and visibility contracts.

## 10. Wave 1.4 closeout decision

The secure image-evidence first slice is **production-proven and closed for new architectural discovery**.

Future work in this domain is extension work, not permission to reopen parallel upload, storage, review, authority or timeline systems.

The next Wave 1 parent sequence item is **Issue #33 — Wave 1.5: Predictive-Health Readiness**.

Wave 1.5 must begin with data-readiness and prediction-governance analysis. It must not begin by training or shipping a predictive model.
