# Aura Wave 1.4 Image Evidence Intake — Release and Operations Runbook

**Issue:** #32  
**Architecture gate:** PR #52  
**Schema gate:** PR #53 / Alembic `c62f1a4e8d30`  
**Runtime boundary:** secure server-mediated raster-image intake only  
**Default state:** disabled

## 1. Purpose

This runbook governs the first production-capable Wave 1.4 evidence intake path.

The feature accepts authenticated JPEG, PNG and WebP uploads only after Aura has resolved the user's vehicle authority, confirmed an allowed evidence purpose, received explicit consent, decoded the raster successfully and re-encoded it without source metadata.

An accepted image is **evidence pending advisor review**. It is not a diagnosis, repair instruction, treatment decision or automatically trusted progression fact.

PDF, audio, direct browser-to-object-storage uploads, public media access, AI extraction and Rina multimodal context remain outside this release boundary.

## 2. Rollout switches

The application ships with evidence intake closed:

```text
EVIDENCE_IMAGE_INTAKE_ENABLED=false
EVIDENCE_STORAGE_PROVIDER=r2
```

`EVIDENCE_IMAGE_INTAKE_ENABLED` is the immediate kill switch. Changing it to `false` must prevent new image ingestion without deleting any previously accepted evidence.

The route may remain registered while disabled; disabled requests return the structured `evidence_intake_unavailable` state.

## 3. Private Cloudflare R2 prerequisites

Before enabling production intake, provision a dedicated R2 bucket for Aura evidence.

Required properties:

- bucket is private;
- no custom public domain or `r2.dev` public access is enabled for this evidence bucket;
- use an R2 API token scoped to the evidence bucket and only the object permissions required by Aura;
- do not reuse a human/global Cloudflare API key;
- do not put the token, account ID or bucket name in GitHub source files;
- retain the bucket/token identifiers in Ajebo Fix's secret-management record so they can be rotated deliberately.

Aura uses the R2 S3-compatible endpoint and does not depend on a public object URL.

## 4. Railway configuration

Add these values to the canonical Aura production service in Railway:

```text
EVIDENCE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=<Cloudflare account ID>
R2_ACCESS_KEY_ID=<R2 token access key ID>
R2_SECRET_ACCESS_KEY=<R2 token secret access key>
R2_BUCKET=<private Aura evidence bucket name>
```

Keep this disabled during initial deployment:

```text
EVIDENCE_IMAGE_INTAKE_ENABLED=false
```

Never paste the secret access key into GitHub issues, pull requests, logs, screenshots intended for public sharing or client-facing messages.

## 5. Deployment order

Use this rollout order:

```text
merge tested code
    ↓
Railway deploy with feature disabled
    ↓
Alembic head confirmed at c62f1a4e8d30 or later compatible head
    ↓
R2 variables present in canonical production service
    ↓
private bucket/token verified outside client flow
    ↓
enable EVIDENCE_IMAGE_INTAKE_ENABLED=true
    ↓
one authenticated owner test upload
    ↓
verify DB row + private object + pending_review state
    ↓
one assigned-driver allowed-purpose test
    ↓
normal monitored operation
```

Do not enable the flag merely because the deployment is green. Production storage and object-authorisation behavior must be verified separately.

## 6. Intake limits and accepted media

First-slice limits:

- accepted decoded formats: JPEG, PNG, WebP;
- raw image bytes: maximum 2 MB;
- decoded image: maximum 25,000,000 pixels;
- any single dimension: maximum 10,000 pixels;
- animated/multi-frame media: rejected;
- SVG, GIF and unknown formats: rejected;
- declared MIME must match the decoded raster format.

Flask's request ceiling is slightly above the file ceiling so multipart framing does not prevent Aura's file-level validator from making the decision.

The server does not trust file extensions or original filenames.

## 7. Sanitization contract

Before private storage, Aura:

1. performs a bounded read;
2. restricts Pillow decoding to the approved raster allowlist;
3. verifies the source image;
4. reopens and fully decodes pixels;
5. rejects decompression-bomb conditions;
6. rejects animation/multiple frames;
7. applies EXIF orientation to the raster;
8. converts to a controlled pixel mode;
9. re-encodes a new image;
10. omits EXIF, XMP and ICC source metadata;
11. calculates SHA-256 over the sanitized object.

The original untrusted upload is never written to R2 by this path.

This is a raster-sanitization boundary, not a claim that Aura has implemented a general-purpose antivirus scanner. PDF/audio remain blocked until their own validation/scanning pipelines are selected and proven.

## 8. Authority and visibility

Every request must preserve Wave 1.3 authority semantics.

### Owner

- may submit approved evidence purposes for an owned active vehicle;
- upload is client-visible;
- cannot place an upload directly into advisor/internal visibility.

### Assigned driver

- may submit `concern_support` and `driver_observation` in this first slice;
- upload is client-visible;
- cannot exercise owner/advisor visibility or treatment authority.

### Advisor

- may use the approved purpose set for a vehicle under advisor authority;
- may select client, advisor or internal visibility under the existing policy boundary.

Authority is resolved **before file bytes are read**. Cross-vehicle attempts fail closed.

## 9. Consent and identity

Production upload requires:

- authenticated Aura session;
- verified account email;
- explicit active-vehicle authority;
- explicit upload consent for vehicle-care storage.

The first source channel is `web` only. WhatsApp/API evidence ingestion must not reuse this route by pretending to be web traffic.

## 10. Database/object-storage consistency

PostgreSQL and R2 cannot participate in one atomic transaction.

Aura therefore records storage state explicitly:

```text
pending
    ↓ successful private write
available
```

Storage write failure:

```text
pending
    ↓
failed / write_failed
```

Database finalization failure after an object write:

```text
attempt object deletion compensation
        ↓
failed / finalization_failed_compensated
```

If compensation itself fails:

```text
failed / finalization_failed_orphan_risk
```

Rows in `failed` state must never be treated as accepted review evidence.

A later reconciliation job must inspect orphan-risk states and compare the database record with private object existence. Do not delete uncertain objects blindly during an incident.

## 11. Privacy-safe logging

Permitted operational log fields include:

- evidence ID;
- vehicle ID;
- authenticated uploader ID;
- resolved authority;
- visibility classification;
- sanitized content type;
- sanitized byte size;
- storage provider identifier;
- safe reason/status code.

Do not log:

- raw media bytes;
- original client filename;
- EXIF/GPS metadata;
- R2 credentials;
- bucket object response bodies;
- full presigned URLs;
- extracted media content;
- prompts/provider bodies.

## 12. Production smoke test

After enabling the flag, perform a single controlled upload from an authenticated owner account using a non-sensitive test image.

Verify:

1. HTTP response is `201`;
2. response says `pending_review` and does not call the image a diagnosis;
3. response does not expose object key, bucket, checksum, secret or source filename;
4. `VehicleEvidence.storage_state = available`;
5. private object exists at the recorded opaque key;
6. stored raster has no source EXIF metadata;
7. an unrelated account cannot use the same vehicle ID to upload;
8. assigned driver can use an allowed purpose but not advisor visibility.

Do not use a real client diagnostic image for the first storage smoke test.

## 13. Incident response

### New uploads failing while Aura otherwise works

1. set `EVIDENCE_IMAGE_INTAKE_ENABLED=false`;
2. leave existing evidence records/objects intact;
3. inspect `storage_state` and safe failure reason distribution;
4. verify Railway variable presence without printing secret values;
5. verify R2 token/bucket scope;
6. verify whether a DB/object compensation failure created `orphan_risk`;
7. forward-fix, then perform a controlled smoke test before re-enabling.

### Suspected storage credential compromise

1. disable evidence intake;
2. revoke/rotate the R2 token in Cloudflare;
3. replace Railway secret values;
4. review R2 object-access/activity evidence available to the account;
5. do not rotate the application database or Rina provider credential unless there is independent evidence they are affected;
6. document the incident and any potentially exposed evidence objects.

### Unexpected public access

Treat any public exposure of the evidence bucket as a privacy/security incident. Disable public access and evidence intake immediately, then assess exposure before normal operation resumes.

## 14. Rollback

Normal rollback is code/flag-first:

```text
EVIDENCE_IMAGE_INTAKE_ENABLED=false
```

Do not destroy the `vehicle_evidence`, `evidence_links` or `evidence_extractions` schema as the routine production rollback. Existing records may represent accepted client evidence and audit continuity.

Do not delete the R2 bucket during application rollback.

## 15. Boundary before the next Wave 1.4 slice

This intake release does **not** make evidence conveniently viewable yet.

Before adding advisor review UI or Rina multimodal reasoning, Aura still needs:

- protected object retrieval;
- deletion/replacement lifecycle;
- storage reconciliation;
- controlled same-vehicle `EvidenceLink` service;
- advisor review workflow and visibility tests.

Only after those boundaries are proven should extraction/transcription or Rina media reasoning be activated.
