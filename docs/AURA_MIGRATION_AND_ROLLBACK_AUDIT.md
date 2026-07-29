# Aura Migration and Rollback Audit

**Issue:** #29  
**Parent epic:** #28  
**Pull request:** #34  
**Repository baseline inspected:** `main` at `19619748b9ff6b3e14ad3dd11793b250cdb4cf1a`  
**Scope:** Documentation and migration policy only; no production behaviour changes

## 1. Executive conclusion

Aura currently has one linear Alembic history with one repository head:

```text
d42e7a1c9b50
```

No branch revision or merge revision was found in the checked-in graph. The repository and CI therefore agree on one expected schema head.

GitHub evidence does **not** directly prove the value currently stored in Railway PostgreSQL's `alembic_version` table. Railway is configured to run the reconciliation script and then `flask db upgrade` before each deployment, and CI asserts that this process finishes at `d42e7a1c9b50`. The exact production value must still be confirmed from a Railway shell or direct database session before the Wave 1.2 migration is deployed.

Wave 1.2 can safely extend `VehicleEvent`, but only through additive, staged migrations. Production rollback must be a code rollback or forward-fix migration—not a destructive Alembic downgrade.

## 2. Complete checked-in Alembic graph

The graph is linear:

```text
433f6c788bde  initial migration
    ↓
63f0dddea696  add consultation notes
    ↓
76b4522c1283  consultation model changes
    ↓
42e597791db4  model changes
    ↓
ac23b4434c19  nullable draft assessment fields
    ↓
b51dbc7679e9  car transmission type
    ↓
78381ef889df  foreign-key/reference repair and chat structures
    ↓
4ea36e64289c  UserMemory changes
    ↓
036d7fd633b4  BookingIntent
    ↓
73313b13c6a6  CarDriver
    ↓
b17e48039864  AccessCode creation
    ↓
0642a7f8ed12  AccessCode expiry
    ↓
b2c74e6b1bfa  AccessCode changes
    ↓
9ec4e4575a0c  DriverCheckIn
    ↓
97892f704afb  driver score
    ↓
7b089086bfe5  ConversationRecord recreation
    ↓
11c9da2fa431  TreatmentPlan
    ↓
ce2520ee6e50  priority access
    ↓
026ea788d410  care plan
    ↓
45ebccb671a2  alert workflow status
    ↓
2951c1bf06c5  AdvisorNote
    ↓
52c8a16467c3  Vehicle Intelligence changes
    ↓
554e83093870  schema check/repair
    ↓
ea28d5290dcb  VehicleProfile table reconciliation
    ↓
68c6eb818ea8  VehicleProfile manufacturer fields
    ↓
1a56c84b07a6  Car and VehicleProfile changes
    ↓
09d596bc12e5  VehicleDTC table reconciliation
    ↓
290c5c28dd7d  VehicleDTC changes
    ↓
82c0c175b392  DiagnosticCodeDefinition
    ↓
b8aee5e13da4  MaintenanceSchedule reconciliation
    ↓
737b4134e370  VehicleRecall reconciliation
    ↓
dd71355ad494  enforce DTC manufacturer NOT NULL
    ↓
4d2b9e7a1c60  email verification
    ↓
7f3a9c2d5e81  session registry
    ↓
c19f2a8b6d41  client profiles
    ↓
d42e7a1c9b50  profile audit events  ← repository head
```

### Metadata inconsistencies

Two migration docstrings contain stale `Revises:` text even though their executable `down_revision` values preserve the linear graph:

- `68c6eb818ea8` says `Revises: 554e83093870` in its header but actually declares `down_revision = "ea28d5290dcb"`.
- `290c5c28dd7d` says `Revises: 1a56c84b07a6` in its header but actually declares `down_revision = "09d596bc12e5"`.

Alembic follows `down_revision`, not the prose header. The headers should be corrected later in a documentation-only cleanup so engineers do not misread the graph.

## 3. Expected production migration state

### Repository head

```text
d42e7a1c9b50
```

### Railway deployment contract

Railway currently executes:

```text
python scripts/reconcile_alembic_history.py && python -m flask db upgrade
```

The reconciliation script handles one known historical production state only:

```text
82c0c175b392
    ↓ conditional reconciliation when both tables already exist
737b4134e370
    ↓ normal Alembic upgrade
d42e7a1c9b50
```

The script refuses to move the version pointer when the expected tables or columns are missing. This is the correct safety posture for that one known drift condition.

### CI expectation

Security CI rehearses the reconciliation and normal migration paths on SQLite and fails unless `alembic_version.version_num` becomes:

```text
d42e7a1c9b50
```

### Production verification still required

Before Wave 1.2, run one of these against Railway production:

```bash
python -m flask db current --verbose
```

or:

```sql
SELECT version_num FROM alembic_version;
```

Expected result:

```text
d42e7a1c9b50
```

Also confirm that the table contains exactly one row. Do not stamp production merely to make the version look correct; first inspect whether the corresponding tables, columns, foreign keys and indexes actually exist.

## 4. Model assumptions versus PostgreSQL constraints

### 4.1 JSON portability is not proven

`models.py` imports `JSON` from `sqlalchemy.dialects.sqlite`, and the initial migration creates several fields with `sqlite.JSON()`:

- `vehicle_events.data`;
- `event_audit_logs.old_data`;
- `event_audit_logs.new_data`;
- `vehicle_health_snapshots.reasons`.

The primary CI migration rehearsal also runs on SQLite. Therefore, successful CI does not prove the checked-in migration history is portable across a fresh PostgreSQL database.

**Wave 1.2 rule:** all new JSON fields must use generic `sa.JSON()` or a deliberately guarded PostgreSQL `JSONB` strategy with PostgreSQL integration tests. Do not add more `sqlite.JSON()` declarations.

### 4.2 `VehicleEvent` is too restrictive for a universal event envelope

The current database contract requires:

- `ownership_id`;
- `event_type`;
- `title`;
- `mileage`;
- `fingerprint`;
- `created_by`.

That works for manual service records, but not every progression event naturally has mileage or a human creator. Examples include consultation transitions, health-signal changes and system-generated correlation events.

**Wave 1.2 direction:**

- keep existing columns for compatibility;
- make `mileage` nullable before non-service events are emitted;
- introduce explicit actor type/authority and nullable actor-user identity;
- keep `created_by` as a legacy compatibility field until all existing readers are migrated;
- do not invent a fake system user merely to satisfy `created_by`.

### 4.3 `EventAuditLog.event_id` is non-nullable

The model and initial migration require `event_audit_logs.event_id` to reference a real `VehicleEvent`.

Current stewardship routes attempt to create audit rows with `event_id=None`. PostgreSQL should reject those writes. This confirms that `EventAuditLog` is not a general-purpose domain audit table.

**Wave 1.2 rule:** stewardship and other domain transitions should emit a canonical `VehicleEvent` first, then link any compatibility audit record to that event. Do not relax `EventAuditLog.event_id` merely to support unrelated audit entries.

### 4.4 Active ownership is not protected by the database

`CarOwnership` uses:

```text
UNIQUE (plate_number, is_active)
```

This does not guarantee one active owner per vehicle. It also couples history integrity to a plate number that may be null or may change.

**Future integrity migration:** add a PostgreSQL partial unique index equivalent to:

```sql
CREATE UNIQUE INDEX uq_car_one_active_ownership
ON car_ownership (car_id)
WHERE is_active = TRUE;
```

Before creating the index, run a duplicate preflight query and stop the migration if any vehicle has more than one active ownership row.

### 4.5 Active health-alert uniqueness prevents clean recurrence history

`VehicleHealthAlert` uses:

```text
UNIQUE (car_id, ownership_id, alert_type, is_active)
```

This allows only one active row, but it also allows only one inactive row for the same vehicle, ownership and alert type. A second create/resolve cycle can collide with the earlier resolved record.

**Future integrity migration:** replace the broad unique constraint with a PostgreSQL partial unique index for active rows only. Preserve all resolved rows as history.

### 4.6 Daily driver check-in uniqueness is application-only

`DriverCheckIn` has no database uniqueness rule. The route checks whether a check-in already exists for the current calendar date, but concurrent requests can still insert duplicates.

A future migration should add an explicit operational date column and enforce uniqueness on:

```text
(car_id, driver_id, checkin_date)
```

Do not build a PostgreSQL index around `DATE(created_at)` until timezone semantics are deliberately defined.

### 4.7 Workflow states are mostly free-form strings

Concern, consultation, assessment, treatment-plan, DTC, alert, maintenance and recall states are generally `VARCHAR` columns without database checks.

Wave 1.2 should not attempt to constrain every historical state in one migration. First inventory and normalise existing values. Add check constraints only after production data passes preflight queries and compatibility aliases are documented.

### 4.8 Existing DTC manufacturer integrity is correctly hardened

Migration `dd71355ad494` backfills null manufacturers to `GENERIC` and then enforces `NOT NULL`, allowing the `(code, manufacturer)` uniqueness contract to work correctly under PostgreSQL null semantics.

That migration is a useful pattern for Wave 1.2:

1. backfill;
2. validate;
3. enforce.

### 4.9 SQLite CI is insufficient for PostgreSQL migration safety

Current CI proves application and migration behaviour on SQLite. It does not currently prove:

- PostgreSQL partial indexes;
- PostgreSQL JSON/JSONB compilation and casts;
- PostgreSQL constraint naming and drop behaviour;
- transactional DDL behaviour;
- lock duration on large table alteration;
- PostgreSQL null/unique semantics under actual production data.

Before merging the Wave 1.2 schema migration, add a PostgreSQL service job that upgrades a fresh database from base to head and upgrades a production-shaped fixture from `d42e7a1c9b50` to the proposed head.

## 5. Safe Wave 1.2 migration shape

Wave 1.2 should use multiple small revisions rather than one large migration.

### Revision A — additive event envelope

Add nullable or server-defaulted fields only:

- `schema_version`;
- `occurred_at`;
- `recorded_at` if `created_at` is not retained as that concept;
- `subject_type`;
- `subject_id`;
- `actor_type`;
- `actor_user_id`;
- `actor_authority`;
- `visibility`;
- `previous_state`;
- `new_state`;
- `progression_direction`;
- `correlation_id`;
- `causation_id`;
- `evidence_refs`;
- `correction_of_event_id`.

Relax `mileage` to nullable. Preserve all old columns and existing records.

### Revision B — deterministic backfill

Backfill existing service/treatment events:

- `schema_version = 1`;
- `occurred_at` from `event_date` when present, otherwise `created_at`;
- `subject_type = 'service_record'` or a reviewed legacy classification;
- `actor_user_id = created_by`;
- `actor_type = 'user'`;
- `visibility` from a conservative default;
- correlation and evidence only when they can be derived without guessing.

Never fabricate state transitions or evidence that the legacy record does not contain.

### Revision C — indexes and constraints

After the application has been writing the new fields successfully:

- add indexes for vehicle/time, subject, correlation and correction relationships;
- enforce `schema_version` and `occurred_at` only after null preflight passes;
- add controlled checks for actor type, visibility and progression direction;
- retain legacy columns until all old readers and templates are removed in a later phase.

### Revision D onward — domain emitters

Move domains onto one canonical emission service incrementally:

1. Reported Concerns;
2. consultations;
3. assessments and treatment plans;
4. DTC occurrences;
5. driver check-ins;
6. stewardship;
7. health-signal lifecycle;
8. communication and reviewed conversation summaries.

Each domain PR must be independently reversible by disabling its emitter without removing event data already written.

## 6. Deployment safeguards

Before applying each Wave 1.2 revision:

1. Confirm production is exactly at `d42e7a1c9b50` or the immediately preceding approved Wave 1.2 revision.
2. Take a Railway/PostgreSQL backup or snapshot and record its identifier in the deployment issue.
3. Run duplicate and null preflight queries.
4. Rehearse the migration against a PostgreSQL copy or production-shaped fixture.
5. Estimate lock behaviour for every `ALTER TABLE` and index operation.
6. Use `CREATE INDEX CONCURRENTLY` through an Alembic autocommit block when production table size makes blocking unsafe.
7. Deploy schema before enabling new event emission.
8. Verify `/healthz`, `/version`, the migration revision and representative owner/advisor/driver workflows.
9. Keep new emitters behind a feature/configuration gate until production verification passes.

## 7. Rollback boundaries

### Allowed rollback

- disable the new event emitter;
- roll back application code to a version that ignores the additive columns;
- preserve new columns and rows;
- correct faulty records through additive correction events;
- ship a forward-fix migration.

### Prohibited production rollback

- `flask db downgrade` across revisions that drop tables or columns;
- dropping the newly extended event columns after production writes begin;
- deleting canonical event rows to restore an older view;
- stamping the database backward without changing the schema;
- renaming or removing legacy columns in the first Wave 1.2 migration;
- restoring a database backup without an explicit incident decision about all writes made after that backup.

The checked-in migration history includes destructive downgrade functions, including migrations that drop whole tables. Therefore Alembic downgrade is not Aura's normal production rollback mechanism.

## 8. Required verification evidence before Issue #30 implementation

The following evidence must be attached to the Wave 1.2 implementation PR:

- output of `flask db heads` showing one head;
- output of production `flask db current --verbose` or the `alembic_version` query;
- PostgreSQL fresh-upgrade CI result;
- PostgreSQL `d42e7a1c9b50 → proposed head` upgrade result;
- preflight duplicate/null reports;
- backup/snapshot identifier;
- upgrade timing and lock observations;
- post-deploy migration version;
- post-deploy event-write and read verification;
- documented feature-gate rollback test.

## 9. Final migration decision for Wave 1.2

The migration graph does not require a new parallel event table or a history reset.

The approved direction is:

```text
Keep one Alembic lineage
        ↓
Start from d42e7a1c9b50
        ↓
Extend VehicleEvent additively
        ↓
Backfill without fabrication
        ↓
Introduce one event-emission service
        ↓
Move domains onto it incrementally
        ↓
Use code rollback and forward fixes, not destructive downgrade
```

Production implementation remains blocked until the exact Railway revision is verified and PostgreSQL migration rehearsal is added.