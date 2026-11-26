# Prince × Aura — Principal Dashboard and Job Assignment

**Scope:** isolated Prince staging only. Do not import real workshop customers into this environment.

## What was added

- `GET /pilot/workshops/<slug>/manage` — principals-only mobile workshop dashboard.
- A tenant-scoped count of active/closed jobs, customers and unreviewed statements **from open jobs**. The page shows up to 40 recent jobs and 12 pending review items and labels those limits.
- A list of eligible operators: users must have an **active operator membership in the exact Prince tenant** and an active User account. In staging, only the two known synthetic operator identities qualify.
- `POST /pilot/workshops/<slug>/jobs/<job_id>/assignment` — principals-only assignment to an eligible operator, with a reason and an expected previous assignee. The server rejects a stale expected assignee (`409`), same-user reassignment (`409`), closed jobs (`409`), outsiders/inactive operators (`404`), malformed or unexpected keys (`400`), and operator-originated attempts (`404`).
- `PrinceJobAssignment` — append-only audit history of who assigned the job, the original user, destination user, reason, and timestamp. Existing prior assignments remain intact and are **not** falsely backfilled.
- Additional existing `PrinceAudit` event `job_assignment_changed` records authenticated principal action. No customer phone/email/home address or bank details are added to those events.
- The Prince demo sign-in now redirects Dad and Stephen to the new management page; Seun/Operator A and the test Operator B continue to their restricted operator page.
- The old operator intake button now reads **Register customer & vehicle**.
- Mobile stylesheet and JavaScript are local static assets, with no remote trackers or external resources.

## Operational boundaries

1. **Seun's job scope changes on his next authenticated request** after reassignment; the prior operator loses access. Staff cannot assign themselves work or select another tenant's staff.
2. Principals retain equal management permissions through **independent accounts**. This is access parity, not a transfer of legal ownership.
3. A job status of `closed` blocks reassignment; status changes are **not introduced in this milestone**.
4. Entries awaiting review are counted only for open jobs. Principal attestation requires the existing explicit review flow and does not imply customer approval, paid invoice, or a verified repair.
5. The demo accepts only fictional names and synthetic reassignment reasons beginning `Demo:` or `Synthetic:`; real names, vehicle identities, contacts, repair details, bank records and Access Bank credentials must not be placed there.
6. A global Aura administrator or technical pilot administrator **is not** a workshop principal.
7. Existing active synthetic assignments are preserved on redeploy. The staging smoke checks are assignment-aware and do not change live demo data.
8. No private customer contact fields are loaded into the principal dashboard itself. Principals use the protected on-demand customer panel within the workshop record view.

## Phone acceptance walkthrough

1. Sign in as **Demo Stephen** or **Demo Workshop Principal**, then verify the new Principal Management Console appears.
2. Review Active jobs, Awaiting review, Customers, and Closed jobs; confirm only fictional Prince records appear.
3. In the job queue, choose another active operator and enter a **fictional reason** (e.g. `Demo: another operator is available for this training job`). Confirm the assignment.
4. Sign out. Sign in as the new operator. The reassigned job should now appear. Sign in as the previous operator: it must no longer be accessible, including via a bookmarked URL.
5. Sign in as the second principal. Both principals should see the same job owner and updated dashboard.
6. Confirm the change did **not** close the job, attest work, issue a receipt, change customer contacts, or update Aura's production vehicle history.
7. Test a second simultaneous reassignment from an out-of-date page; the server must reject it and ask for a reload.

**Remaining work:** principal-authorized job state transitions; owner-approved scope of work; estimates/invoices/payment verification; evidence storage and retention; principal-only user provisioning and revocation UI; security and privacy reviews for live client data; verified Aura vehicle record bridge. The dashboard is a pilot operations UI, not a complete production workshop ERP.
