# Prince restricted live workshop pilot

Status: NOT READY FOR REAL DATA. Release issue #361. Baseline clinical handoff revision fe4f0a5 (12 successful CI workflows). This branch does not enable the pilot or change any environment.

## First release scope
Workshop-only intake, assigned jobs, technical entries and principal review, estimates, customer authorization, invoices, manual payment verification, receipts, closure. No global Aura linking, clinical projection, health scores, Rina, or customer-facing portal. Clinical handoff remains training-only. Preserve the existing demo and Aura production deployments.

## Implementation acceptance contract
1. Provision a dedicated live-pilot application and database only after confirming budget and configuration. No copying demo database or production customer data. Synthetic staging remains untouched. Default access denied until explicit release approval.
2. Implement a dedicated staff login and environment-bound tenant guard; individual staff credentials and account recovery, verified principals and operators, least privilege, revocation and session checks. Do not reuse demo credentials or gate. Deny all unrelated Aura routes and clinical handoff routes in this environment. Check server-side CSRF, rate limiting, encrypted private fields, safe logs and no-store responses.
3. Establish controller/processor responsibilities, approved privacy notice and lawful basis, minimum data fields, customer rights and retention process. Keep intake disabled until documented principal approval and notice version. No government IDs, payment credentials, or unnecessary identity evidence. Keep private contacts out of free-text entries and external logs.
4. Backups and tested recovery, isolation checks, incident response and stop/rollback procedure. Prove a revoked operator cannot access any customer data. Confirm operator visibility is limited to assigned work, no cross-tenant access, and principals have appropriate oversight.
5. Remove training-only wording from live-specific templates only after implementing the separate live mode; quotations and receipts must accurately identify pilot status, recorded approvals, amounts and manual payment verification. Do not weaken synthetic guardrails.

## Acceptance rehearsal (disposable data first)
| ID | Role | Action | Expected |
|---|---|---|---|
| A01 | Principal | Sign in with individually provisioned account | Private tenant dashboard only |
| A02 | Operator A | Create intake after notice acknowledgment | Assigned job with minimal record |
| A03 | Operator B | Request A's job directly | Denied without disclosure |
| A04 | Operator A | Enter complaint, observation and proposed work | Source labels retained; principal review required |
| A05 | Principal | Review work, approve estimate | Versioned approval, exact totals |
| A06 | Principal | Record customer decision | Authorization scoped to approved estimate |
| A07 | Principal | Issue invoice and verify payment | Correct balance and receipt; duplicate payment rejected |
| A08 | Principal | Close job with outstanding concerns | Follow-up remains visible; no claim of verified repair outcome |
| A09 | Any staff | Request global Aura or clinical routes | Denied; no writes to global records |
| A10 | Principal | Revoke operator and test existing session | Subsequent access denied |
| A11 | Authorized administrator | Restore backup in disposable environment | Records and audit history reconciled |
| A12 | Principal | Check printed estimate and receipt | Matches ledger; no hidden private information |

## First real case after approval
Select one consenting customer. Explain purpose, access, retention and contact for questions using the approved notice; record required acknowledgment. Use existing paper/manual process in parallel. Reconcile amounts and job status before expanding. Record defects using anonymized case IDs, not names, phone numbers, VINs or screenshots with personal details in GitHub. Stop new intake if unauthorized access, data loss, or financial mismatch is detected. Keep the existing workshop records available.

## Explicit unresolved release decisions
Privacy notice, retention and staff account roster are not yet approved; no real data is permitted until the implementation and acceptance checks above are completed. No production merge, deployment or live credentials are authorized by this document. Automated Aura transfer remains a separate project tracked by PR #360.
