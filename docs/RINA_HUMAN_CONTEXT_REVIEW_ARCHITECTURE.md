# Rina Human Context Review Architecture

## Goal

Rina should understand a vehicle's wider historical context like an experienced Ajebo Fix advisor while remaining strict about what becomes durable history.

The governing principle is:

> **Understand broadly. Write narrowly.**

## 1. Vehicle-wide conversational context

During historical review, Rina may use the selected vehicle's relevant imported evidence, candidate episodes, active and unapplied drafts, recorded durable history, and advisor corrections to understand what the advisor means.

This read context is broader than the active write target.

Rina must not treat the current draft as the only thing she is allowed to understand.

## 2. One active write target

At any moment, only one historical episode/draft is the active write target.

Changing conversational focus does not merge episodes. Switching targets preserves the previous draft and its corrections.

## 3. Natural episode switching

The advisor should be able to say things such as:

- "Review the 25 July steering episode from Evidence #101."
- "Switch to the August belt job."
- "Let's work on the July steering issue first."

If one eligible episode uniquely matches, Aura switches the governed review target automatically.

If the advisor mentions another dated episode naturally but the intent to switch is not clear, Rina asks one short clarification instead of rejecting the turn or sending the advisor back through menus.

Cross-reference language such as "keep this separate from the July episode" must not switch the active target.

## 4. Clarify instead of reject

When a message may belong to another episode, Rina should respond like a human collaborator:

> "It sounds like you're talking about the 25 July steering episode rather than the August belt episode I have open. Should I switch?"

The advisor can answer naturally, for example "yes", "switch to it", or "stay here".

## 5. Evidence is not completed work

Receipts, estimates, payments, recommendations and authorised scope are evidence about an episode. They are not automatically completed interventions.

A standalone source can seed a recordable work candidate only when the source analysis itself classifies that work as completed and provides a structured action.

Otherwise Rina enters clarification/intake mode and asks the advisor what actually happened.

## 6. Persistent corrections

Advisor corrections belong to the draft they were made against and survive temporary conversational switching.

Examples:

- "electric steering rack belt, not drive belt";
- "diagnostic faults were cleared, but no steering-pump repair was performed";
- corrected historical dates;
- part condition or component wording.

Returning to that draft should reuse those corrections rather than asking the advisor to repeat them.

## 7. Durable-write boundary

None of the conversational freedoms above weaken Aura's authority controls.

Rina may:

- understand;
- compare;
- switch focus;
- ask questions;
- correct drafts;
- preserve context;
- prepare a final record.

Rina may not write durable historical truth until the governed draft is complete and the authorised advisor explicitly says **Confirm and record**.

Vehicle scope, source provenance and advisor authority remain mandatory at write time.

## 8. UX boundary for uploads

The browser must stay connected only until Aura confirms **Source secured**.

After that point, server-side analysis may continue while the advisor leaves the page or locks the device.

If the browser disconnects before receiving storage confirmation, Aura must not claim that nothing was stored; it should tell the advisor to check Historical Sources before retrying.
