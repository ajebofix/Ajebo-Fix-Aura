# Aura · Vehicle-Aware Animated Hero — Implementation Brief

**Status:** Implemented initial family-aware asset system on a staging feature branch; production remains unchanged.  
**Scope:** Flask/Jinja vehicle record (`templates/car_detail.html`), shared owner and advisor/admin render path.  
**Parent baseline:** `qa/aura-signature-staging-access` (staging branch built atop Signature UI PRs #339–#341).

## Product promise

Every vehicle-record page must visibly relate to the vehicle being viewed. Motion should be calm, premium, accessible, and honest. **No existing capability, button, route, permission, record, benefit, or care function may disappear or fail because of the illustration.** Visualisation is decorative, not live diagnostics.

## Delivered in this first implementation

- **Original, local SVG illustration family library:** S-Class, G-Class, GLE, GLK, C-Class, E-Class, GL/GLS, ML, plus executive-sedan, SUV and neutral fallbacks.
- **Deterministic renderer:** `services/vehicle_hero.py::resolve_vehicle_hero(car)` uses recorded brand and model; uses recorded vehicle-profile body style for fallback if needed. It neither decodes VINs nor makes clinical/telemetry claims.
- **Shared UI:** `templates/components/vehicle_hero.html` included in the original top hero of `templates/car_detail.html` for both owner and advisor/admin, after the three existing primary actions.
- **Lightweight motion:** CSS drift (2 px) and understated light wash; zero additional JS/WebGL/Lottie/network calls; CSS `prefers-reduced-motion` disables all motion. Existing content and action targets remain unchanged.
- **Truthful disclosure:** Each family silhouette states that it is a family-style illustration and **not an exact trim, model-year or live vehicle image**. Unknown models are never relabelled as known models.
- **Regression suite:** `tests/test_vehicle_hero.py` checks core family mappings, non-Mercedes aliases, fallbacks, asset presence, accessibility/motion contracts and authenticated owner page rendering.

### Asset paths
```text
static/vehicle-heroes/
  mercedes/{s-class,g-class,gle,glk,c-class,e-class,gl-gls,ml}.svg
  fallback/{sedan,suv,generic}.svg
```

### Examples
| Stored vehicle identity | Resolved asset | Truthful presentation |
|---|---|---|
| Mercedes-Benz S 450 | `mercedes/s-class.svg` | S-Class family illustration |
| Mercedes-Benz G63 AMG | `mercedes/g-class.svg` | G-Class family illustration |
| Mercedes-Benz GLE 450 | `mercedes/gle.svg` | GLE family illustration |
| Mercedes-Benz GLK 350 | `mercedes/glk.svg` | GLK family illustration |
| Lexus RX, recorded body style SUV | `fallback/suv.svg` | SUV category illustration |
| Unsupported model without body style | `fallback/generic.svg` | Unspecified vehicle illustration |

**Current fidelity limit:** This first working iteration differentiates *families*, not exact S 450 vs S 500 or G 63 vs G 550 trims, years, body kits, colours, equipment, or actual vehicle photos. Do not describe the assets as pixel-accurate replicas. The architecture supports adding exact year/trim assets later after licensed, accurately sourced artwork and identification evidence are available.

## Technical acceptance gates

1. **Identity correctness:** S-Class must not show G-Class; G-Class must not show saloon. Unknown models must not receive a fabricated match. Run parametrized model resolver tests and sample asset review.
2. **Feature parity:** Health overview, history, Rina, DTC, consultations, care pathway, billing, advisor tools, owner/driver authority, PDF flows and source uploads must retain their former behavior and permissions. Run existing non-regression suites.
3. **Performance:** No JS animation library, heavy video, external asset URL, layout shifts or network-bound rendering. SVG local to app; illustration has intrinsic `width`/`height`.
4. **Mobile:** On iOS Safari widths 320 / 375 / 390 px and Android Chrome, inspect clipping, hero height, touch-target access and portrait display. Also verify 768 / 1024 / 1440 px.
5. **Accessibility:** Text contrast, non-empty useful alternative text, visible controls, animation-free presentation under reduced-motion, meaningful static state.
6. **Integrity:** UI never claims a live diagnostic scan or changes recorded vehicle health. Synthetic test data only for staging.

## Release process

- Commit and PR against `qa/aura-signature-staging-access`, not `main` or the production Railway service.
- Run Signature UI, Rina, authority, billing and security CI and authenticated mobile staging checks against the **final tested SHA**.
- Check model-specific appearance and regression with synthetic S 450, G 63 (create synthetic example only in staging for this QA) and unsupported vehicle. Confirm production runtime/branch is unchanged.
- Keep feature in staging while QA is incomplete; merge/production deployment requires explicit approval.

## Follow-up enhancements (not represented as already shipped)

- Exact model generation-specific artwork (e.g. W221/W222/W223 S-Class and W463/W463A G-Class) only when provenance and generation mapping are verified.
- Add licensed/commissioned illustration sets for BMW, Lexus, Range Rover and other families; verify each against actual physical body proportions.
- Optional owner-provided vehicle photography with explicit consent and separate privacy/storage design.
- Capture click-through mobile QA evidence, error states, network throttling and accessibility audit results.

## Implementation files

`services/vehicle_hero.py`, `templates/components/vehicle_hero.html`, `templates/car_detail.html`, `app.py`, `static/css/aura-vehicle-record.css`, `static/vehicle-heroes/**`, `tests/test_vehicle_hero.py`, `.github/workflows/ui-ux-contract-ci.yml`.
