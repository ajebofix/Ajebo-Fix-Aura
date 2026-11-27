# Prince live operational acceptance — 26 November 2026

Status: staff rehearsal infrastructure online; real data release NOT approved.

## Observed infrastructure evidence
- Dedicated Railway project `1bd12284-d667-4655-8484-3c1e03136432`, environment `7b3c3fc4-2b24-4e4a-ba37-6fde7293bcd1`.
- App deployment `e06d1d72-10a1-4a4f-ae43-78181862618f` SUCCESS, runtime log confirms commit `f0fe16492822524cab3a7c30d21d062336d0584c`, environment prince-live-pilot and PostgreSQL. Migration log shows upgrade through `f26b0c4d837e`, Gunicorn one worker started. Railway reports both services online, zero failures/warnings/critical issues.
- PostgreSQL deployment `202eda3e-cb4d-4b83-a298-5df7792ae9e1` SUCCESS. Prior networking readback confirms no public domain or TCP proxy; app also has no public domain.
- Latest initial memory samples: app 0.142 GB, PostgreSQL 0.125 GB, PostgreSQL disk 0.159 GB. These are startup/idle observations, not monthly average or load validation. Limits: app 1 GB/0.5 vCPU, database 0.5 GB/0.5 vCPU, one replica each. Retain until actual load data; caps do not reserve usage or cap spend. No extra services.

## Human acceptance gates (not completed)
1. Authorized Railway administrator must establish independently generated strong application secret and Fernet key in Railway secret controls, retain recovery copies securely and verify secrecy before exposing the app. Do not use chat or GitHub for credentials. All release flags remain false.
2. Verify the named staff roster, identity and control of each staff email. In the private Railway service shell for the exact app, invoke `python -m scripts.prince_provision_live_staff` once per person. Password entry is interactive and hidden; do not use CLI flags, shared passwords or log outputs with personal values. Verify both principal and operator access and revocation. No staff records have been created by the deployment.
3. Create a baseline backup in Railway's Postgres volume controls; perform recovery in a separate disposable environment before any live data. Do not restore over live records. Record only anonymized validation outcomes. Backup retention, cost and recovery objective require principal approval.
4. Approve the privacy notice and retention, rights and incident procedure; provide it to each participating customer. Existing paper/customer-facing billing records remain authoritative. Define the minimum intake scope, no private contacts or VIN linkage, no global Aura transfer. No live release switch may be enabled until the acceptance requirements are documented and satisfied.
5. Expose only the staff app via HTTPS when secure configuration and access validation are ready; never expose PostgreSQL. Then use one consenting customer's record in parallel with the existing process, reconcile and review before expansion.

Connector limitation: available Railway operations do not provide an interactive live service shell or volume backup/restore controls. The authenticated Railway administrator must perform these steps in Railway or its CLI. No backup or restore is claimed, and no public staff access has been created.
