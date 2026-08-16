# Aura Wave 1.4 Private Evidence Access — Release and Operations Runbook

**Issue:** #32  
**Prerequisite:** secure image intake merged and kept feature-gated  
**Scope:** protected retrieval, advisor-governed deletion, storage reconciliation  
**Public object URLs:** prohibited

## 1. Purpose

This runbook governs how Aura may retrieve or delete already-sanitized private evidence objects after the first image-intake slice.

The storage bucket remains private. Aura does not expose a Cloudflare R2 object URL, `r2.dev` URL, permanent link or unauthenticated bearer download route. Access is always mediated by the Aura application and re-authorized against the current vehicle relationship.

Evidence review and care-workflow linking remain separate later boundaries. Retrieval does not convert pending evidence into accepted professional evidence.

## 2. Feature gates

All new runtime surfaces ship closed:

```text
EVIDENCE_RETRIEVAL_ENABLED=false
EVIDENCE_ADVISOR_DELETION_ENABLED=false
```

Retrieval also requires an explicit short-lived grant policy:

```text
EVIDENCE_RETRIEVAL_GRANT_SECONDS=<positive integer, maximum 300>
```

Aura has no runtime default for grant lifetime. Missing, zero, negative or greater-than-300 values fail closed.

Do not enable deletion merely because retrieval is enabled. Deletion is a separate operational authority and risk boundary.

## 3. Protected retrieval flow

The first retrieval contract is POST-only:

```text
verified authenticated session
        ↓
POST /evidence/<id>/grant
        ↓
current vehicle authority + visibility check
        ↓
short-lived signed grant bound to user ID + evidence ID
        ↓
POST /evidence/<id>/content with grant token
        ↓
re-check current vehicle authority
        ↓
re-check evidence lifecycle/storage state
        ↓
private storage read through EvidenceStorageProvider
        ↓
byte-size + SHA-256 verification
        ↓
Aura returns an attachment response
```

The grant token is not an object-storage credential and is not placed into a GET query string by this release.

A grant issued while a driver is assigned does not preserve access after the driver relationship is revoked. Authority is resolved again before the private object is read.

## 4. Visibility policy

### Owner

An active vehicle owner may retrieve evidence with `visibility=client` for that vehicle.

### Assigned driver

The first slice is intentionally stricter for drivers: an active assigned driver may retrieve only that driver's own `client` evidence. A driver does not automatically gain access to another driver's or owner's uploaded media merely because the media is client-visible.

### Advisor / administrator

Advisor/administrator authority may retrieve client, advisor and internal evidence while the professional authority contract remains valid.

### Lifecycle restrictions

Evidence cannot be retrieved when:

- `review_status=deleted`;
- `deleted_at` is set;
- `storage_state` is not `available`;
- required integrity metadata is missing.

## 5. Retrieval integrity

Before Aura returns bytes to the authenticated caller, the retrieval service verifies:

- private object size equals `VehicleEvidence.byte_size`;
- SHA-256 of the retrieved bytes equals `VehicleEvidence.sha256`.

If either check fails, retrieval fails closed and the evidence row moves to:

```text
storage_state=failed
storage_failure_reason_code=retrieval_integrity_mismatch
```

Do not allow a mismatched object to reach advisor review, client display or Rina context.

## 6. Private response behavior

Successful content delivery is an attachment response from Aura itself.

The response may identify the safe evidence filename and review state. It must not expose:

- R2 bucket name;
- object key;
- storage credentials;
- source/original filename;
- permanent object URL;
- checksum unless a future explicit operational surface needs it.

Application-wide `Cache-Control: no-store` remains in force.

## 7. Advisor-governed deletion

This release does **not** invent client self-deletion rules. Evidence deletion has privacy, professional-record and linked-care consequences, so the first runtime deletion surface is advisor/administrator only.

The deletion request must use an approved reason code:

```text
invalid_upload
duplicate
privacy_request_approved
retention_expired
superseded_cleanup
operational_correction
```

Immediate deletion is blocked when:

- evidence is already `accepted` as professional evidence;
- any `EvidenceLink` exists.

Those cases require the later governed review/unlink workflow rather than silent record destruction.

## 8. Deletion state machine

For deletable evidence, Aura first commits a logical tombstone:

```text
review_status=deleted
deleted_at=<time>
reviewed_by_user_id=<advisor/admin>
reviewed_at=<time>
review_reason_code=<approved reason>
storage_state=delete_pending
```

Only after the tombstone is committed does Aura attempt private-object deletion.

Successful object deletion:

```text
delete_pending → deleted
```

Storage deletion failure:

```text
delete_pending
```

The object is immediately non-retrievable through Aura because logical deletion already occurred. `delete_pending` tells operations that physical object cleanup still requires reconciliation.

## 9. Reconciliation

Use the manual reconciliation command from a controlled Railway/operations environment:

```bash
python scripts/reconcile_evidence_storage.py --limit 200
```

The command prints counts only. It does not print object keys, media content, bucket names or secrets.

Current reconciliation responsibilities:

- `delete_pending`: delete object if it still exists, then mark storage `deleted`;
- `failed / finalization_failed_orphan_risk`: remove any orphan object and retain a failed audit row with `orphan_cleanup_completed`;
- `available` with missing object: mark failed with `missing_object`.

No background scheduler is activated by this release. Operational automation can be added only after the reconciliation contract is proven in production.

## 10. Railway rollout order

Keep all evidence features disabled while deploying code:

```text
EVIDENCE_IMAGE_INTAKE_ENABLED=false
EVIDENCE_RETRIEVAL_ENABLED=false
EVIDENCE_ADVISOR_DELETION_ENABLED=false
```

Before retrieval activation:

1. confirm the canonical production app is on the intended commit;
2. confirm PostgreSQL is on `c62f1a4e8d30` or a later compatible head;
3. confirm the private R2 variables exist without printing their values;
4. configure an approved `EVIDENCE_RETRIEVAL_GRANT_SECONDS` of 300 seconds or less;
5. verify a sanitized non-sensitive test evidence object exists privately;
6. enable only `EVIDENCE_RETRIEVAL_ENABLED=true`;
7. test owner grant/content flow;
8. revoke a test driver relationship after grant creation and prove the grant no longer retrieves bytes;
9. verify no object URL/key appears in browser/client responses.

Deletion activation should happen later and separately:

1. keep retrieval stable;
2. test deletion on non-sensitive pending test evidence;
3. prove object-delete failure remains `delete_pending` and inaccessible;
4. run reconciliation and prove the pending state closes;
5. only then set `EVIDENCE_ADVISOR_DELETION_ENABLED=true` for normal operations.

## 11. Incident response

### Retrieval unexpectedly denied

Check, without exposing sensitive values:

- current owner/driver/advisor relationship;
- evidence visibility;
- review status and `deleted_at`;
- storage state;
- grant lifetime configuration;
- storage provider name/configuration.

Do not extend grant lifetime or weaken visibility merely to make a failed request work.

### Integrity mismatch

Treat `retrieval_integrity_mismatch` as an evidence-integrity incident:

1. disable evidence retrieval if mismatches are not isolated;
2. do not serve the affected object;
3. compare expected evidence metadata with storage object history/operations evidence;
4. preserve the failed database row;
5. do not silently overwrite the checksum to match the unexpected object.

### Delete pending backlog

1. keep the logically deleted evidence inaccessible;
2. verify private-storage health/configuration;
3. run reconciliation in a controlled shell;
4. investigate any persistent failures before retrying at scale.

### Storage credential compromise

Disable intake/retrieval/deletion, rotate the R2 token, update Railway secrets and assess private-object access. Do not print or copy compromised token values into GitHub or chat logs.

## 12. Rollback

Runtime rollback is flag-first:

```text
EVIDENCE_RETRIEVAL_ENABLED=false
EVIDENCE_ADVISOR_DELETION_ENABLED=false
```

Do not drop evidence tables or delete the R2 bucket as routine application rollback. Existing evidence records, tombstones and storage-state audit continuity must be preserved.

## 13. Next boundary

After this access layer is proven, Wave 1.4 may proceed to:

- advisor review state transitions;
- same-vehicle `EvidenceLink` creation with subject existence/vehicle proof;
- canonical event/progression linkage;
- client-safe reviewed evidence presentation.

PDF/audio and AI/Rina media reasoning remain separately gated.
