# Rina advisor workspace and verified identity

Aura's professional Rina surface separates two valid contexts:

1. **Advisor overview** — no vehicle is selected and no vehicle record is loaded.
2. **Explicit vehicle context** — one vehicle is selected, authority is rechecked and only that vehicle's permitted record is available to Rina.

This keeps administrator/advisor account help useful without forcing a vehicle selection, while preserving the authority-first rule that vehicle records are never inferred from free text.

## Behaviour

- Ask Rina is available from navigation, the Advisor Console and vehicle pages.
- Administrator/advisor accounts start in **Advisor overview**. They may ask verified account or workflow questions without selecting a vehicle.
- `/chat/account` remains deterministic, provider-free and vehicle-free. It does not restore a vehicle binding, load vehicle/chat history or save account-help text into a vehicle record.
- Professional vehicle selection uses bounded server-side search rather than preloading a fleet dropdown. Search accepts client name, VIN, plate, make or model and returns at most 20 authorised results.
- Duplicate year/make/model vehicles are disambiguated with the active client name, plate number and VIN tail.
- Administrators retain Aura's existing broad administrator access. Dedicated advisor accounts only discover vehicles linked by persisted consultation, assessment, treatment-plan or advisor-note records.
- Selecting a result re-runs `resolve_rina_authority`; search results do not themselves grant access.
- Returning to Advisor overview explicitly clears the short-lived Rina vehicle and conversation binding from the session.
- Owner/driver accounts keep explicit vehicle selection based on their persisted ownership/driver relationships.
- Free-text chat still cannot silently switch the selected vehicle. Conversational vehicle resolution remains deferred until it can preserve the same explicit authority and ambiguity controls.
- Vehicle identity questions use the saved display name, actual account role and recorded vehicle relationships. Administrator access is not presented as ownership.
- Client and advisor vehicle chat histories remain separated by user and vehicle.
- Manual and decoded vehicle labels display the year once.

## Validation requirements

The release must keep the existing Rina authority, chat-cutover, provider, memory, Advisor 360, CSRF and security suites green. Additional regressions cover:

- administrator search by client name;
- duplicate vehicle disambiguation;
- advisor search exclusion for unlinked vehicles;
- professional-search denial for owner/client accounts;
- minimum professional search length;
- vehicle-free greeting and advisor-capability help;
- explicit clearing of the persisted Rina vehicle/conversation binding.

## Rollout

No schema migration or new environment variables are required. `RINA_ADVISOR_360_ENABLED` is unchanged. Deployment health and the signed-in Advisor Workspace journey should be checked after release.
