# Aura Signature UI — Design System v1.0 (Phase 1)

**Product:** Aura by Ajebo Fix  
**Intent:** A distinctive private automotive health experience, inspired by the quiet precision of luxury automotive interfaces and the approachable clarity of modern mobile operating systems. This is an Aura design language, not a reproduction of Mercedes-Benz or Apple UI, logos, or trade dress.

## Core qualities
- **Private:** restrained navigation, minimal chrome, strong information boundaries.
- **Clear:** vehicle state, owner/driver/advisor authority and one meaningful next step.
- **Calm:** no ornamental alarms, pulsing status indicators or hover movement for basic navigation.
- **Premium:** precision spacing, strong type, tactile controls and considered negative space.
- **Accessible:** visible keyboard focus, status in words as well as color, 44px minimum action targets, reduced-motion support, mobile-safe scrolling.

## Foundations

| Token | Value | Use |
|---|---|---|
| Obsidian | `#111820` | Navigation and dark hero |
| Porcelain | `#F4F3F0` | Primary canvas |
| Pure white | `#FFFFFF` | Content surfaces |
| Aura Blue | `#315D76` | Primary actions and focused control |
| Silver | `#91ACBC` | Restrained accents |
| Ink | `#1A252E` | Primary copy |
| Slate | `#56636D` | Secondary copy |
| Divider | `#DFE3E4` | Neutral separation |
| Verified/healthy | `#18765C` | Positive state text/action |
| Attention | `#946523` | Attention state text/action |
| Critical | `#B14242` | Critical state text/action |

**Typography:** System UI stack (Apple system on supported devices, Inter fallback). 15–16px body, 13px secondary labels, 27–36px primary page title, 17–23px card headings.

**Structure:** 258px desktop sidebar; readable content max width 1420px; 20px cards; 12px buttons; 44px minimum interaction targets. Responsive drawer at ≤900px.

**Motion:** only functional transition cues; no repetitive floating/breathing/shimmer on utility screens. Honor `prefers-reduced-motion`.

## First-wave coverage

1. Shared shell — dark nav, page hierarchy, buttons, cards, fields and status pills.
2. Owner home — signature hero with real destinations and authority-aware vehicle cards.
3. Loading/splash — calmer neutral brand palette.
4. Existing feature/permission behavior — **unchanged**, including role gating, vehicle selection, CSRF, financial actions, and underlying data.

No generated health data, estimates, or fabricated vehicle states are introduced.

## Follow-up design passes (separate PRs)

- **Owner vehicle record:** a quiet vehicle identity hero, care status, one next action, clear provenance and timeline; preserve real unknowns and verification labels.
- **A.J. Rina workspace:** first-class conversation, vehicle scope visible, source/authority affordances; calm loading and failure states.
- **Advisor console:** priority triage first, then consultations and client context; denser information with trustworthy visual rhythm, distinct from owner home.
- **Forms and historical records:** unify labels, validation, table density, empty states and mobile actions.
- **Theme options:** only after validating the bright porcelain/dark navigation baseline with actual owners and advisors.

## Release checklist

- [ ] Screenshot-review at widths 320, 375, 390, 768, 900, 1024 and 1440px.
- [ ] Verify owner, driver, advisor and admin roles after login.
- [ ] Verify navigation, real vehicle selection, Rina, forms, Billing manual workflows and security screens.
- [ ] Verify keyboard focus, VoiceOver labels and visible color contrast.
- [ ] Run Jinja compile, route contract, CSS contract and security tests.
- [ ] No database migrations, payment actions, sends, or permission changes.
- [ ] Merge only following visual review and passing CI.
