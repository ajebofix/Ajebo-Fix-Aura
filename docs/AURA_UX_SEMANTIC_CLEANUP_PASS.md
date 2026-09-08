# Aura UX / Semantic Cleanup Pass

This focused pass removes presentation wording that could imply a stronger or different system state than Aura actually stores.

## Included

- Driver flash messages are rendered once through the shared base template.
- Mileage-history badges distinguish advisor observations, accepted reports, rejected reports, pending reports, historical evidence, and other recorded observations.
- Care-signal lifecycle events use the `Care signal` category instead of the generic `service` badge.
- Care-signal lifecycle events no longer display the generic `completed` event-row status because the lifecycle state is already stated by the event title.
- The advisor odometer screen uses the canonical stored vehicle display name instead of appending the model year a second time.

## Follow-up identity-label cleanup

The main vehicle-health template still combines `decoded_display_name` with `year`. Because `decoded_display_name` can itself fall back to `display_name`, which already includes the year, a dedicated vehicle identity-label normalization should remove that ambiguity across all templates and Rina vehicle selectors rather than patching one screen at a time.
