# Aura Wave 1.4 Evidence Interaction — First-Use Runbook

**Scope:** owner image submission → private storage → advisor private preview → controlled advisor review → reviewed vehicle record  
**Media:** JPEG, PNG, WebP only  
**Default production state:** disabled until policy and private storage are configured

## 1. What this workflow does

The first product interaction slice lets a verified owner submit one supporting image from the vehicle record and lets an advisor privately open and review pending image evidence from the advisor vehicle record.

The UI does not create a parallel evidence system. It orchestrates the existing Wave 1.4 boundaries:

```text
vehicle authority
    ↓
explicit upload consent
    ↓
server-side image validation + sanitization
    ↓
private object storage
    ↓
pending_review
    ↓
short-lived authenticated retrieval grant
    ↓
private content delivered through Aura
    ↓
advisor accept / reject
    ↓
canonical evidence.reviewed event
    ↓
reviewed evidence record projection
```

Submitting, opening or accepting an image does not establish a mechanical diagnosis, failed component or repair instruction.

## 2. Required runtime controls

The interaction surface composes existing controls rather than inventing a master switch:

```text
EVIDENCE_TIMELINE_ENABLED=false
EVIDENCE_IMAGE_INTAKE_ENABLED=false
EVIDENCE_RETRIEVAL_ENABLED=false
EVIDENCE_ADVISOR_REVIEW_ENABLED=false
```

Supporting policy/configuration remains required:

```text
EVIDENCE_RETENTION_DAYS=<approved positive integer>
EVIDENCE_RETRIEVAL_GRANT_SECONDS=<approved positive integer up to 300>
EVIDENCE_STORAGE_PROVIDER=r2
R2_ACCOUNT_ID=<configured privately>
R2_ACCESS_KEY_ID=<configured privately>
R2_SECRET_ACCESS_KEY=<configured privately>
R2_BUCKET=<private evidence bucket>
```

There is no production retention duration or retrieval-grant duration implied by this runbook. Ajebo Fix must approve those values before activation.

## 3. Safe activation order

Do not enable all flags at once on an unproven deployment.

Recommended order:

```text
1. Deploy code with all evidence flags OFF
2. Confirm PostgreSQL is at current Alembic head
3. Confirm approved retention policy is configured
4. Confirm private R2 bucket/token configuration
5. Perform private-storage smoke verification
6. Set EVIDENCE_TIMELINE_ENABLED=true
7. Confirm reviewed-record page renders safely
8. Set EVIDENCE_RETRIEVAL_ENABLED=true
9. Confirm advisor private retrieval using non-sensitive test media
10. Set EVIDENCE_ADVISOR_REVIEW_ENABLED=true
11. Confirm controlled review moves test evidence into reviewed record
12. Set EVIDENCE_IMAGE_INTAKE_ENABLED=true
13. Perform one owner submission with non-sensitive test media
14. Confirm the complete owner → advisor → reviewed-record flow
```

Enabling intake last prevents users from being invited to submit evidence before Ajebo Fix can privately retrieve and govern it.

## 4. Owner workflow

When the timeline and image-intake flags are enabled, a verified owner sees **Add evidence for review** on the vehicle record.

The first UI allows only:

- Support a reported concern
- Support a consultation

The browser gives convenience checks for JPEG/PNG/WebP and 2 MB maximum size. The server-side sanitizer remains authoritative. A browser check passing never guarantees acceptance.

The owner must explicitly consent to private storage for the vehicle-care purpose.

A successful submission remains `pending_review` and is not shown as reviewed evidence until professional review is completed.

## 5. Advisor workflow

A verified advisor may see a pending-image queue for the vehicle.

Safe pending metadata may include:

- evidence ID;
- controlled purpose;
- uploader display label;
- upload time;
- content type and byte size;
- visibility classification.

The queue must never expose:

- R2 object key;
- bucket name;
- source/safe filename;
- SHA-256 checksum;
- credentials;
- raw media bytes;
- extracted content;
- diagnosis text.

## 6. Private preview

The advisor's **Open private image** action must continue using Aura's two-step protected retrieval path:

```text
POST retrieval grant
    ↓
short-lived user/evidence-bound token
    ↓
POST token to private content endpoint
    ↓
current authority checked again
    ↓
private bytes returned through Aura
```

The browser creates a temporary in-memory object URL solely for the review modal and revokes it when the preview closes.

No R2 URL is inserted into the page or returned to the browser.

The UI reveals review controls only after that evidence item has been opened successfully in the current page session. This is a professional workflow guard only; backend review authority remains independently enforced.

## 7. Review decisions

Accepted reason codes:

```text
advisor_verified
sufficient_for_record
```

Rejected reason codes:

```text
insufficient_quality
not_relevant
wrong_vehicle
duplicate
privacy_restriction
```

Do not add free-text diagnosis to this form.

`accepted` means suitable for Aura's care record. It does not mean an image proves root cause or confirms a failed component.

## 8. Mobile behavior

The interaction UI is designed for narrow screens:

- upload/review fields collapse to one column;
- action buttons become full-width;
- private preview is bounded to the viewport;
- the image uses contained scaling rather than cropping.

The first proven media allowlist does not include HEIC/HEIF. Do not silently add it just because many iPhones capture HEIC. A future HEIC slice must prove decoder support, resource limits, metadata stripping and safe re-encoding first.

## 9. Production smoke test

Use non-sensitive test media first.

Verify in order:

1. owner vehicle record shows upload control only after intake is deliberately enabled;
2. unsupported type or oversized image is rejected;
3. accepted upload returns pending-review state;
4. advisor vehicle record shows safe pending metadata only;
5. page source contains no object key, bucket, checksum or permanent media URL;
6. private preview succeeds through authenticated POST retrieval;
7. unrelated account cannot retrieve the evidence;
8. advisor can record one controlled review decision;
9. reviewed evidence disappears from pending queue;
10. reviewed evidence appears in the safe vehicle record;
11. Reported Concern progression is unchanged merely because evidence was reviewed;
12. logs contain IDs/safe status codes only, never media bytes or private credentials.

## 10. Failure handling

### Upload UI visible but uploads fail

- disable `EVIDENCE_IMAGE_INTAKE_ENABLED`;
- keep the reviewed-record surface available if safe;
- check retention configuration and private R2 variables without printing secret values;
- inspect safe storage-state/reason-code distribution;
- fix and smoke-test before re-enabling intake.

### Private preview fails

- disable `EVIDENCE_RETRIEVAL_ENABLED` if failures are systemic;
- keep accepted historical records visible;
- verify current authority, grant lifetime configuration and private storage access;
- do not replace the private flow with a public R2 URL as a workaround.

### Review fails after preview

- keep evidence pending;
- inspect controlled review conflict/error state;
- do not manually edit review columns in production to force completion.

## 11. Next boundary

After this interaction is proven, the next product addition may let an advisor link already-accepted evidence to an existing same-vehicle Reported Concern from the vehicle record.

That linkage must reuse the existing same-vehicle `EvidenceLink` service and canonical `evidence.linked` event. It must not infer root cause or automatically advance concern progression.
