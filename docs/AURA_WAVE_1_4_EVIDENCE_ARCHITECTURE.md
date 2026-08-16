# Aura Wave 1.4 — Secure Multimodal Evidence Architecture

**Issue:** #32  
**Parent epic:** #28  
**Status:** Architecture decision — implementation not yet authorised  
**Scope:** Images, PDF documents and voice notes as controlled vehicle evidence  

## 1. Decision summary

Wave 1.4 introduces one canonical evidence domain for Aura.

The core rule is:

> **Uploaded media is evidence, not a diagnosis.**

A file may support a reported concern, consultation, assessment, treatment action or canonical vehicle event, but neither the upload itself nor an AI extraction may silently become professional truth.

This architecture extends the existing Wave 1.2 and Wave 1.3 contracts rather than creating a parallel permission, memory or event system.

### Locked decisions

1. Add one canonical `VehicleEvidence` record for uploaded evidence metadata and lifecycle state.
2. Add a separate `EvidenceLink` concept for controlled linkage to Aura care subjects rather than adding file columns across every domain table.
3. Add a separate `EvidenceExtraction` concept for optional transcription/document/image extraction. Provider output never overwrites the evidence record or advisor-reviewed conclusions.
4. Reuse Wave 1.3 vehicle authority before every upload, read, link, review or delete operation.
5. Reuse Wave 1.2 `VehicleEvent` for material progression after a reviewed evidence action; do not create a parallel event timeline.
6. Use private object storage behind an `EvidenceStorageProvider` interface.
7. Select **Cloudflare R2 as the first production storage backend**, while keeping Aura storage logic provider-neutral.
8. Keep the R2 bucket private. No public bucket, `r2.dev` exposure or public object URL is permitted.
9. Initial Wave 1.4 uploads are server-mediated and validated before accepted storage. Direct browser-to-R2 upload is deferred until a quarantine/finalisation protocol is separately designed and tested.
10. Extraction is optional, provenance-bearing, confidence-aware and reviewable. Rina may explain reviewed/extracted evidence within existing authority limits but may not diagnose from it.

## 2. Why a new evidence domain is justified

The current-system audit confirms that Aura already has canonical models for vehicles, concerns, consultations, assessments, treatment plans, Vehicle Intelligence, health snapshots, events and Rina memory, but it does not have a canonical uploaded-media/evidence model.

The secure client-profile implementation deliberately stayed on initials rather than weakening upload security before private object storage existed. Wave 1.4 therefore fills a real architectural gap rather than duplicating an existing attachment system.

## 3. Domain ownership

Wave 1.4 spans three existing architecture domains:

- **Advisor Operations** owns evidence review, visibility and care-workflow linkage.
- **Rina Intelligence** may consume only authority-approved, minimised evidence/extraction context.
- **Communication and Channel Infrastructure** may later deliver evidence from WhatsApp or future channels, but channels do not own evidence truth or storage policy.

Vehicle Intelligence may reference reviewed evidence when appropriate, but provider-sourced automotive knowledge and user-uploaded evidence remain distinct provenance classes.

## 4. Canonical evidence lifecycle

```text
Authenticated user
      ↓
Explicit vehicle scope
      ↓
Wave 1.3 authority resolution
      ↓
Purpose + consent/lawful-purpose declaration
      ↓
Server-side byte limits and type validation
      ↓
Safe decode / normalisation / metadata stripping where applicable
      ↓
Malware/content safety gate where required
      ↓
Private object write
      ↓
VehicleEvidence = pending_review
      ↓
Optional extraction/transcription
      ↓
Advisor review
      ├── accepted
      ├── rejected
      └── superseded
      ↓
Approved EvidenceLink to care subject / canonical event
      ↓
Client-safe presentation according to visibility policy
```

No unreviewed extraction may mutate a consultation assessment, treatment plan, DTC interpretation, health conclusion or client-facing professional recommendation.

## 5. Proposed canonical data model

This PR does not add the schema. It locks the responsibilities that the migration PR must implement.

### `VehicleEvidence`

One row represents one uploaded evidence object and its controlled lifecycle.

Required fields should include:

```text
id
car_id
uploaded_by_user_id

evidence_type
purpose
source_channel
visibility
review_status

storage_provider
object_key
safe_display_name
content_type
byte_size
sha256

captured_at              nullable; only when trustworthy
capture_time_source      nullable; e.g. user_declared / embedded_verified
uploaded_at

consent_basis
lawful_purpose
retention_until
deleted_at

reviewed_by_user_id      nullable
reviewed_at              nullable
review_reason_code       nullable

created_at
updated_at
```

### Controlled vocabularies

Initial evidence types:

```text
image
document
audio
```

Initial source channels:

```text
web
whatsapp
api
```

Only `web` is implemented in the first slice. The others reserve provenance vocabulary; they do not authorise a channel implementation.

Initial review states:

```text
pending_review
accepted
rejected
superseded
deleted
```

Initial visibility values must reuse Aura's existing policy vocabulary where compatible:

```text
client
advisor
internal
```

Purpose must be a controlled value rather than arbitrary free text. The first implementation should cover the minimum care workflows actually present, for example concern support, consultation support, assessment evidence, treatment evidence, diagnostic/service document and driver observation.

### `EvidenceLink`

Evidence must not gain one nullable foreign key for every current and future care model.

`EvidenceLink` owns the approved relationship between evidence and an Aura subject.

Conceptual fields:

```text
id
evidence_id
car_id
subject_type
subject_id
relationship_type
created_by_user_id
created_at
```

`subject_type` is a controlled registry, initially limited to:

```text
reported_concern
consultation
assessment
treatment_plan
vehicle_event
```

The linking service must resolve the referenced subject and prove that it belongs to the same `car_id`. A raw `subject_id` supplied by a client is never trusted on its own.

### `EvidenceExtraction`

Extraction/transcription is a child record, not a field that overwrites uploaded evidence.

Conceptual fields:

```text
id
evidence_id
extraction_type
provider
provider_model
provider_request_id
status
confidence
extracted_text_or_structured_result
provenance
created_at
reviewed_by_user_id
reviewed_at
review_status
```

The implementation must decide which extracted values require encryption or a separate protected payload because extracted text can contain personal, vehicle or location information.

Multiple extraction attempts may exist. Advisor-reviewed output remains authoritative over provider output.

## 6. Authority model

Every evidence operation begins with the Wave 1.3 authority resolver.

### Owner

May upload evidence for an actively owned vehicle, view client-visible evidence for that vehicle, and request deletion subject to retention/legal/operational constraints.

### Driver

May upload operational evidence only for an active assigned vehicle and may view only evidence that policy exposes to that driver. Driver authority never grants owner financial records, advisor-only evidence or treatment approval.

### Advisor

May upload and review evidence only within an explicitly authorised vehicle/workflow scope. Advisor review may accept/reject/link evidence but must remain auditable.

### Administrator

May perform governed operational/incident actions, but administration is not a bypass around evidence audit, vehicle scope or retention policy.

### Revocation

If ownership, driver assignment or advisor scope is revoked, new access must fail immediately even if the user previously possessed an object URL. This is why Aura must authorise before generating any temporary retrieval URL.

## 7. Storage provider decision

### Selected first backend: Cloudflare R2

R2 is selected because Aura's master architecture already identifies Cloudflare as the intended future edge/storage/security provider and R2 offers an S3-compatible object API suitable for a provider abstraction.

Cloudflare documents that:

- R2 buckets are private by default and public access requires explicit enablement;
- objects and metadata are encrypted at rest automatically;
- access is protected in transit with TLS;
- presigned URLs can grant temporary operation-specific access without exposing API credentials;
- R2 supports S3-compatible API access, allowing Aura to avoid hard-coding a Cloudflare-specific storage surface into domain services.

References:

- https://developers.cloudflare.com/r2/buckets/public-buckets/
- https://developers.cloudflare.com/r2/reference/data-security/
- https://developers.cloudflare.com/r2/api/s3/presigned-urls/
- https://developers.cloudflare.com/r2/api/s3/api/

### Provider abstraction

Domain code must depend on a narrow interface such as:

```text
EvidenceStorageProvider
  put_validated(...)
  open_private(...)
  create_read_grant(...)
  delete(...)
  exists(...)
```

Provider credentials, bucket names and endpoints belong in Railway environment/secret configuration, never in GitHub.

The database stores `storage_provider` and opaque `object_key`, not a permanent public URL.

### Bucket policy

The production evidence bucket must:

- remain private;
- have `r2.dev` disabled;
- have no public custom-domain object access;
- use a least-privilege API credential scoped to the evidence bucket;
- separate production from development/test evidence;
- apply lifecycle/retention rules only after Aura's retention policy is approved;
- never permit directory-style user-controlled keys.

### Object-key strategy

Aura generates object keys. Original filenames are never used as object paths.

Conceptually:

```text
production/evidence/<random-id>/<derived-safe-name>
```

The random identifier must be non-guessable. Car ID, client name, registration number, VIN, phone number or concern text must not appear in the key.

## 8. Upload transport decision

The first implementation uses a server-mediated upload path because validation must occur before an object becomes accepted evidence.

For the initial conservative limits:

1. browser sends multipart upload to Aura over HTTPS;
2. Aura re-authorises user + vehicle;
3. Aura enforces request/body limits before full processing;
4. Aura validates magic bytes/content type and decodes the supported format;
5. images are decoded and re-encoded into an approved output format with metadata stripped;
6. document/audio validators confirm permitted format and safety gates;
7. only validated bytes are written to private storage;
8. database metadata and object write follow an explicit compensation strategy so failed transactions do not silently orphan accepted objects.

Future direct-to-R2 presigned PUT may be introduced only with a private quarantine namespace, strict content constraints, finalisation callback, validation before promotion, expiry/cleanup and abuse controls. It is not part of the first slice.

## 9. Format boundary

### Images

Initial allow-list:

- JPEG
- PNG
- WebP

Required controls:

- magic-byte validation;
- actual image decode;
- decoded pixel/dimension limits;
- server-side re-encode;
- metadata/EXIF stripping;
- rejection of animated/unsupported encodings unless deliberately added later;
- reject SVG regardless of extension or declared MIME type.

### PDF

PDF is accepted only after structural validation and the malware/content-safety gate defined by the implementation PR. Aura must not render untrusted active PDF content inline by default.

### Audio

The first slice may accept a deliberately small set of common voice-note formats only after safe decoder support and duration/size limits are locked.

### Explicitly rejected in Wave 1.4 first slice

- SVG
- HTML
- JavaScript
- office macro formats
- executable/archive formats
- arbitrary ZIP/RAR/7z
- unrestricted video
- URL ingestion / remote fetch
- any type that cannot be safely decoded and normalised by the approved pipeline

## 10. Retrieval decision

Evidence is never served from a Flask static/uploads directory and never gets a permanent public URL.

The request path is:

```text
User requests evidence
      ↓
Aura authenticates
      ↓
Aura re-resolves vehicle authority
      ↓
Aura checks evidence visibility + lifecycle state
      ↓
Aura creates a short-lived retrieval grant
      ↓
Client receives object through temporary private access
```

Presigned URLs are bearer tokens. Expiry must therefore be short for sensitive evidence. The exact duration belongs in implementation configuration and tests rather than being embedded across templates.

## 11. Review and professional-truth boundary

`pending_review` evidence may be visible as a submitted item but must be labelled as unreviewed.

Only accepted evidence may influence professional/client-facing conclusions through a governed service.

Review must capture:

- reviewer identity;
- timestamp;
- accepted/rejected/superseded state;
- reason code;
- approved subject linkage;
- resulting canonical event where the review materially changes a care workflow.

Advisor review never changes the underlying media bytes. Corrections are additive records/events.

## 12. Rina and AI extraction boundary

Media processing must not be bolted directly into `/chat`.

The sequence is:

```text
VehicleEvidence
      ↓
EvidenceExtraction provider abstraction
      ↓
provenance + confidence + status
      ↓
advisor review where required
      ↓
Rina provider context receives only policy-approved minimised facts
```

Rules:

- raw media is sent to an AI provider only when the operation requires it and policy permits it;
- provider output is untrusted data, not instructions;
- text embedded in images/PDFs/audio cannot override Rina system/authority policy;
- extraction failures or low-confidence results remain explicit;
- no extraction may declare a mechanical diagnosis, failed component or repair instruction autonomously;
- no model action may approve assessment/treatment or alter authority;
- provider identifiers/version and evidence IDs must be preserved for auditability;
- no raw media, extracted sensitive content, prompt or provider response body belongs in ordinary application logs.

## 13. Canonical event integration

Wave 1.4 reuses Wave 1.2 rather than creating an `EvidenceEvent` table.

Material evidence actions may emit controlled `VehicleEvent` events after the event taxonomy is deliberately extended, for example:

```text
evidence.submitted
evidence.reviewed
evidence.linked
evidence.superseded
evidence.deleted
```

The implementation PR must not add these event names casually. It must extend the canonical event-emission registry, visibility rules, idempotency contract and PostgreSQL constraints together.

Evidence bytes are never copied into `VehicleEvent.data`. Event evidence references contain IDs/provenance only.

## 14. Transaction and orphan handling

Object storage and PostgreSQL do not share a transaction.

The implementation therefore requires an explicit compensation protocol.

Preferred first-slice pattern:

```text
validate bytes
  ↓
create pending DB record / deterministic object key
  ↓
write private object
  ↓
finalise DB metadata
```

If the database finalisation fails after object write, the object must be queued/marked for cleanup and never exposed as accepted evidence. If object write fails, the evidence record must remain failed/unavailable or roll back according to the service contract.

Deletion follows the same rule: database state first marks deletion intent, storage deletion is attempted idempotently, and completion is audited. No route should perform an untracked best-effort delete.

## 15. Retention, deletion and consent

The schema must support retention; the architecture does not invent a universal retention duration in this PR.

Before production rollout, Aura must define policy by purpose and evidence class, including:

- why the evidence is collected;
- who may access it;
- whether the owner/client may request deletion;
- when operational/legal needs require temporary retention;
- whether extracted derivatives are deleted with the source;
- how superseded evidence is treated;
- how storage lifecycle rules reconcile with database audit history.

Deletion of bytes does not require deletion of the minimal audit/event fact that an evidence action occurred, provided that record contains no retained sensitive payload beyond approved policy.

## 16. Data-location boundary

Bucket location/jurisdiction is a deployment and privacy decision, not something to infer from a Cloudflare location hint.

Cloudflare documents automatic placement, best-effort location hints and separately defined jurisdictional restrictions. Before the production evidence bucket is created, Aura must record the chosen deployment location/jurisdiction and complete the required privacy/data-transfer review for the intended client base.

Reference:

- https://developers.cloudflare.com/r2/reference/data-location/

## 17. Observability

Permitted structured operational fields include:

```text
request_id
user_id
car_id
evidence_id
evidence_type
byte_size
review_status
storage_provider
operation
outcome
reason_code
latency_ms
```

Prohibited logs include:

- object bytes;
- presigned URL query strings;
- storage secrets;
- original/raw filenames where they contain personal data;
- extracted document/audio text;
- prompt/model response bodies;
- VIN/registration/client name unless a separate operational need is approved and minimised.

## 18. Migration boundary

The next schema PR must be additive and reversible in development rehearsal.

It should create the minimum evidence tables and controlled constraints without backfilling invented evidence from historical records.

No current concern, consultation, assessment, treatment, event or profile row should be silently reclassified as uploaded evidence.

PostgreSQL fresh-upgrade and production-shaped upgrade/downgrade-rehearsal gates are mandatory before merge.

## 19. Delivery sequence after this architecture PR

```text
PR 1  Architecture + threat model              ← this PR
PR 2  Evidence schema + PostgreSQL migration
PR 3  Storage adapter + secure image intake
PR 4  PDF/audio validation + retrieval/deletion
PR 5  Advisor review + EvidenceLink workflow
PR 6  Optional extraction/transcription adapters
PR 7  Canonical event/Rina integration + closure
```

The exact split may be tightened further, but schema, storage safety, review and extraction must not be merged into one mega-PR.

## 20. Definition of architecture-ready

Wave 1.4 implementation may proceed only when this document and the companion threat model establish agreement on:

- one canonical evidence model;
- private-storage ownership;
- Cloudflare R2 as the first backend behind an abstraction;
- authority before upload/retrieval;
- accepted file boundary;
- malware/polyglot and metadata controls;
- review states;
- retention/deletion semantics;
- provider/extraction safety boundary;
- event integration without a parallel timeline;
- transaction/orphan strategy;
- privacy-safe observability.

Until those decisions are reviewed, adding upload buttons or AI media handling is explicitly out of scope.
