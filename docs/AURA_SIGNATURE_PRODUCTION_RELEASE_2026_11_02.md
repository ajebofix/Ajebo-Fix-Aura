# Aura Signature UI — Production Release Candidate (2 November 2026)

## Scope and approval

This candidate combines the previously reviewed, stacked design phases:
- Dashboard / global Signature UI (PR #339)
- Vehicle record without decorative car illustration (PR #340)
- Rina Presence and conversational workspace (PR #341)
- Approved iPhone login logo sizing and compact mobile navigation from the tested staging branch.

**Explicitly excluded:** `static/vehicle-heroes/**`, `services/vehicle_hero.py`, `templates/components/vehicle_hero.html`, `scripts/seed_signature_staging.py`, staging passwords, test accounts, fictional vehicles and other features from rejected draft PR #342.

The product keeps the existing record health, Rina conversations, navigation, billing, consultation, evidence, advisor and owner actions intact. All server-side roles/permissions continue to determine access. No migrations or schema changes are introduced by this UI candidate.

## Release controls

- Release PR must be against current `main`; deploy only after the final **single** merge, avoiding separate production releases for phase 1 / 2 / 3.
- CI: latest exact head SHA must pass Aura UI & UX Contract CI, Rina, security, billing and any other applicable checks.
- Check production baseline identity and status (`/healthz`, `/version`) before and after merge.
- Confirm active Railway production service remains linked to `main`, while staging retains its own environment/project, database and test accounts.
- Keep the previous healthy Railway deployment available for rollback. Any regression in login, privileged access, vehicles, invoicing, Rina or critical actions blocks completion.
- On actual iPhone Safari, test owner, driver and administrator/advisor paths, navigation (open and close), login and Rina composer.
- A successful health endpoint proves the app runs; it is **not** proof that every production workflow was tested.

## Handmade Blender models — later separate release, not part of this release

Ajebo Fix may create original car assets in Blender after securing approved references, checking model year/chassis/generation and trim, recording dimensions, building calibrated geometry against multi-angle photographic evidence and inspecting the resulting proportions. Use reviewed renders/video/360 frames optimized for mobile, rather than unlicensed meshes or approximate line art. No illustration should appear before independent visual approval. Actual vehicle photos are also a valid honest fallback where rights and permission are established.

## Rollback

Previous good Railway deployment identity must be captured immediately before merge; if critical features fail, use Railway deployment rollback and investigate whether database or configuration issues require separate corrective steps. Never migrate/copy staging clients into production.
