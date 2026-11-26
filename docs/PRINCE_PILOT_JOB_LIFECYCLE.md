# Prince × Aura — principal-controlled job lifecycle

**Environment:** dedicated synthetic-only Prince staging; not approved for actual workshop data.

## Stages

Draft → Awaiting approval → Approved → In progress → Closed.

Both principal accounts have equal permissions to advance a job by one stage. Operators may record authorized job statements but cannot read the private principal decision ledger or change the stage. Technical administrators cannot advance jobs. Approving a stage is **internal workshop scope approval**, not evidence of customer authorization, payment, successful repair or roadworthiness.

Every transition requires an explicit written decision reason. Before starting work, the job must have an active eligible operator. Skipping stages, stale transitions, duplicate transitions, cross-tenant access, and changing a closed job are rejected.

## Closure

- At least one workshop statement and a principal review of **every** recorded statement are required.
- A closure decision is either `follow_up_required` or `no_follow_up_identified`.
- Outstanding concerns, temporary interventions, recommended next actions, needs-evidence decisions, unconfirmed statements and reported work without attestation prevent the no-follow-up route. The no-follow-up route also requires at least one reported-work statement attested by a principal.
- Follow-up closure requires a separate written plan identifying the next action and responsible person. Principals must address unclassified earlier notes in their closure summary; the system does not infer that those notes are resolved.
- A closed job with a follow-up plan appears in a separate management queue, not the active jobs count.
- Closure is administrative. The original notes, attestations and assignment events remain unchanged. No job or vehicle is automatically declared repaired, safe, paid or released.
- The original review and job-entry append APIs lock the same job row as lifecycle changes in PostgreSQL, avoiding a closure concurrent with a new statement/review.

## Persistence and limits

`f12c9e48a620` adds the tenant-scoped `prince_job_transitions` ledger with foreign keys, stage-path constraints and principal actor/time/reason. Existing jobs and prior assignments are not backfilled or changed. Status reasons and follow-up plans are principal-only; operators see the current stage via their existing safe job projection. The dashboard lists the newest 20 recorded follow-up plans and counts all of them.

The pilot uses a forward-only lifecycle: no reopen/correction, cancellation, customer authorization workflow, independent evidence validation, follow-up completion workflow, billing, payment verification or production Aura health synchronization yet. Follow-up plans remain in the queue until a dedicated follow-up workflow is implemented. The system records declarations, not independent diagnostic evidence.

## Acceptance walkthrough

1. Sign in as Demo Stephen or Demo Workshop Principal and open Job progression.
2. Move a fictional job through the allowed stages using reasons starting `Demo:` or `Synthetic:`.
3. Confirm an operator sees the stage but no progression controls; direct lifecycle access must return 404.
4. Attempt closure before reviewing all entries: it must fail. Record and review an explicitly fictional outstanding concern, then confirm no-follow-up closure fails.
5. Select follow-up required and enter a fictional closure reason and plan. Verify the job is closed, history retained, and the plan remains in the follow-up queue.
6. Verify a stale page cannot change the stage, a closed job cannot be reassigned or receive new entries, and no financial or production vehicle record is created.

CI must pass on SQLite and isolated PostgreSQL, including the migration and staging rehearsal, before the tested revision is promoted to Prince staging. Actual customer data and bank credentials are prohibited in this demonstration.
