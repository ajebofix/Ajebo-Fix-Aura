# Aura Wave 2.2A2 — Consultation Route Cutover

Parent issue: #77

## Purpose

Bind Aura's existing consultation URLs to the Wave 2.2A lifecycle service without changing public URLs or fabricating historical state.

## Runtime cutover

The existing URL rules remain stable. During admin blueprint registration, the Wave 2.2A2 compatibility adapter replaces the bound view functions for the migrated endpoints with thin lifecycle-service adapters.

Migrated endpoints:

- owner consultation booking
- owner priority scheduling request
- advisor direct consultation scheduling
- advisor consultation start
- advisor consultation completion
- advisor consultation queue

A new advisor endpoint accepts an existing `requested` consultation and confirms its schedule.

## Why a compatibility adapter exists

`cars/routes.py` and `admin/routes.py` contain broad legacy route surfaces. Rewriting those modules simultaneously with lifecycle semantics would enlarge the migration blast radius. Wave 2.2A2 therefore cuts over the runtime endpoint bindings first, proves them with route-level tests and CI, and leaves deletion of the now-unbound legacy mutation bodies to a later cleanup-only change.

This adapter does not own lifecycle legality. `ConsultationLifecycleService` remains the only owner of consultation state transitions and canonical event emission.

## Transaction boundary

For owner requests:

1. lifecycle service creates `requested` + canonical `consultation.requested`;
2. the latest unfinished BookingIntent is completed in the same SQLAlchemy transaction;
3. caller commits once;
4. WhatsApp/client/admin notifications are attempted only after commit.

Notification failure does not roll back a durable consultation request.

Advisor schedule/start/complete transitions similarly call the lifecycle service and commit once.

## Queue semantics

`requested` is now an explicit advisor queue bucket.

For owner-originated requests, `scheduled_for` temporarily stores the owner's preferred time because the production column is still non-null. The queue labels it **Preferred time** and explicitly states that it is not a confirmed appointment.

Only after an advisor confirms the request does the lifecycle move to `scheduled`.

Unknown/historical statuses are quarantined into a non-scheduled review bucket rather than silently being presented as scheduled appointments.

## Safety boundary

- no Rina/provider transition authority;
- no synthetic consultation-event backfill;
- no internal advisor summary copied into canonical client-visible event payloads;
- no communication channel owns state;
- no owner request is represented as an advisor-confirmed appointment.

## Follow-up cleanup

After production verification, delete the unbound legacy consultation mutation bodies from `cars/routes.py` and `admin/routes.py` in a cleanup-only PR. That cleanup must not change runtime semantics.