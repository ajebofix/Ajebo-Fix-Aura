# Aura Signature UI — Rina Presence (Phase 3)

## Product feeling
Rina should feel like a private automotive concierge: calm, capable, considerate, and precise.
Visual inspiration: the restraint of premium automotive digital interiors, the fluid tactile clarity of modern mobile operating systems. Aura retains its own graphic language rather than adopting another manufacturer's marks or trade dress.

**Future-facing ≠ sci-fi.** Keep the interface alive through micro-interactions that correspond to real user-visible states, not synthetic urgency, human impersonation, ambient monitoring claims, or dashboard noise.

## Signature details
- **Rina Presence:** an abstract silver-blue orb on an obsidian header, never a robot face.
- **Truthful motion:** slow resting animation; gentle response glow only during an actual pending request (including advisor context actions). Text reflects loading state without claiming that a vehicle is being monitored in real time.
- **Private context:** vehicle context selected explicitly through existing owner/driver/advisor/administrator authority controls. No automatic vehicle binding and no widening of permissions.
- **Conversation clarity:** neutral assistant messages; deep graphite user messages; no gaudy gradient chat boxes; familiar reading width and thumb-friendly composer.
- **Practical confidence:** conspicuous non-diagnostic disclaimer; advisor review and physical inspection remain applicable.
- **Accessibility:** contrast-aware type, >= 44px controls, visible focus, reduced-motion override, status announcements, responsive composer and message bubbles.

## Implementation boundary
Visual layer: `static/css/aura-rina-presence.css`, imported after the two previous Aura Signature phase layers. Template updates touch Rina's hero and live *UI* busy presentation only. The existing `/chat`, `/chat/account`, vehicle selection, authority-banner, history, historical-copilot, booking CTA, speech features, CSRF and response rendering are unchanged.

The shared Rina chat component is also used on other pages, so review the dedicated workspace, dashboard, vehicle record and advisor contexts before merge.

## Verification checklist
1. Run source guardrail tests, existing Rina chat-cutover, authority and UI contract tests, plus CI.
2. Authenticated desktop (1440/1024) and iOS Safari (390/375/320); avoid keyboard overlap, cut-off selection panels or message overflow.
3. Check active owner, owner with no vehicle, driver, advisor overview, advisor selected client vehicle, revoked access and long answers.
4. Verify loading state visibly starts and ends on both success and error, with accurate status text and no infinite activity.
5. Check keyboard focus order, screen-reader announcements, reduced-motion and mobile Enter/Shift+Enter behavior.
6. Request design approval before stacking is squashed or deployed; this branch is based on the vehicle-record UI branch.
