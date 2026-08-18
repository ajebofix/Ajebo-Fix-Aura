# Aura Wave 1.5 — Predictive-Health Threat Model

**Status:** Architecture / safety gate  
**Runtime implementation:** Not approved  
**Companion:** `AURA_WAVE_1_5_PREDICTIVE_HEALTH_READINESS_ARCHITECTURE.md`

## 1. Purpose

Predictive-health errors can change human behaviour even when no repair action is executed automatically.

A false warning may create unnecessary anxiety, inspection cost or loss of trust. A missed warning may create false reassurance. A confident-looking pattern can also be mistaken for diagnosis even when the underlying data is sparse or biased.

This threat model therefore treats predictive output as a high-consequence **decision-support claim**, not as an ordinary UI feature.

## 2. Assets to protect

Wave 1.5 must protect:

- vehicle-scoped longitudinal truth;
- owner/driver/advisor privacy boundaries;
- reviewed professional authority;
- canonical event provenance;
- historical `as_of_time` correctness;
- outcome/label integrity;
- model/rule version integrity;
- confidence and abstention semantics;
- client trust and non-diagnostic product positioning;
- rollback capability.

## 3. Trust boundaries

```text
client / driver observations
        ↓
canonical Aura records
        ↓
reviewed professional outcomes
        ↓
readiness feature generation
        ↓
rules baseline / future model
        ↓
advisor-only prediction review
        ↓
possible future client-safe projection
```

No later layer may upgrade the authority of an earlier layer merely by processing it.

Examples:

- an uploaded image does not become diagnosis because a vision model describes it;
- client-reported recurrence does not become confirmed mechanical recurrence without the appropriate reviewed record;
- provider DTC text does not become vehicle-specific root cause;
- Rina language does not become a training label merely because it sounds authoritative.

## 4. Threat: false certainty from sparse data

### Failure mode

A model or rule produces a confident result because the available examples are few, homogeneous or poorly labelled.

### Harm

- unnecessary escalation;
- false reassurance;
- advisor anchoring;
- client anxiety;
- unsupported marketing claims.

### Controls

- readiness report before implementation;
- minimum evidence requirements defined per target after inventory;
- explicit abstention;
- confidence calibration;
- rules baseline;
- no client-facing prediction during readiness/shadow mode.

## 5. Threat: label leakage

### Failure mode

Future information enters the input features, making evaluation look much better than real deployment.

Examples:

- using a final concern state to predict final concern state;
- using treatment completion to predict successful treatment completion;
- using an advisor review recorded after the prediction time;
- using backfilled records whose original availability time is unknown.

### Controls

- every feature generated relative to `as_of_time`;
- time-forward evaluation where appropriate;
- explicit feature/label availability timestamps;
- leakage review as a required readiness deliverable;
- reproducible feature versioning.

## 6. Threat: cross-vehicle contamination

### Failure mode

Events, memory, outcomes or evidence from one vehicle are used to produce or explain a prediction for another vehicle without an approved aggregate-learning boundary.

### Harm

- privacy breach;
- incorrect prediction;
- authority bypass;
- impossible-to-audit reasoning.

### Controls

- vehicle scope is explicit in every prediction contract;
- active-path authority re-check;
- vehicle-held-out evaluation where relevant;
- no raw cross-client record retrieval at prediction time;
- aggregate training datasets must be separately governed and de-identified where appropriate.

## 7. Threat: source-authority inflation

### Failure mode

Low-authority or unverified data is treated as equivalent to professional reviewed fact.

Sources at risk include:

- client free text;
- driver observations;
- unreviewed evidence;
- generic DTC definitions;
- stale provider data;
- AI-generated summaries;
- legacy rows with unclear provenance.

### Controls

- source/provenance classification;
- input weighting/eligibility declared in the prediction contract;
- unknown/unverified sources cannot silently become labels;
- professional corrections preserved separately;
- explanations cite supporting canonical records.

## 8. Threat: correlation presented as causation

### Failure mode

A recurring pattern is presented as the cause of a mechanical condition.

Example:

```text
Coolant loss often preceded temperature alerts
```

becoming:

```text
The coolant leak caused the current fault
```

without professional confirmation.

### Controls

- prediction targets must describe observable risk/pattern states, not unsupported root cause;
- client wording remains non-diagnostic;
- supporting evidence is shown separately from causal claims;
- advisor review required before operational escalation.

## 9. Threat: false positives

### Potential harms

- unnecessary inspections;
- avoidable cost;
- client anxiety;
- excessive advisor workload;
- alert fatigue;
- erosion of trust.

### Required review

Each candidate target must document:

- false-positive definition;
- plausible user/operational harm;
- acceptable threshold rationale;
- escalation cost;
- mitigation and rollback.

A model with strong recall but intolerable false-positive burden is not production-ready.

## 10. Threat: false negatives

### Potential harms

- false reassurance;
- delayed professional review;
- missed maintenance deterioration;
- safety exposure if wording implies all-clear.

### Controls

- no prediction output should imply absence of mechanical risk beyond the observed evidence;
- abstention and uncertainty remain visible;
- advisor and inspection workflows remain authoritative;
- client-safe wording must not convert `no_supported_pattern` into `vehicle is safe`.

## 11. Threat: feedback loops and self-fulfilling labels

### Failure mode

Aura prioritises certain vehicles, advisors inspect them more often, those vehicles therefore accumulate more recorded issues, and the model learns that its own prior prioritisation predicts risk.

### Controls

- distinguish organic observations from system-triggered follow-up;
- preserve intervention/exposure metadata;
- evaluate selection bias;
- keep shadow-mode predictions separate from clinical outcomes;
- do not train directly on system-generated priority without causal review.

## 12. Threat: advisor confirmation bias

### Failure mode

An advisor sees a prediction before forming an independent review and unintentionally confirms it.

### Controls

- shadow-mode evaluation may hide predictions until after independent review where feasible;
- record advisor conclusion separately from model output;
- preserve timestamps/order of prediction and review;
- measure disagreement rather than overwriting the original result.

## 13. Threat: missing-outcome / survivorship bias

### Failure mode

Only vehicles that return for follow-up have known outcomes. Vehicles that leave the platform disappear from evaluation, making success or risk estimates misleading.

### Controls

- report loss-to-follow-up explicitly;
- do not treat missing outcome as successful resolution;
- measure outcome completeness by cohort;
- candidate targets with severe missingness must be deferred.

## 14. Threat: stale vehicle context

### Failure mode

Prediction uses old mileage, maintenance state, DTC status, ownership, evidence or concern state.

### Controls

- prediction contract defines freshness requirements;
- stale required input triggers abstention;
- output records `as_of_time` and source timestamps;
- later display must show that the result was based on a historical state, not current certainty.

## 15. Threat: provider drift / external-data change

### Failure mode

A VIN/DTC/recall/maintenance provider changes definitions, coverage or response schema and silently alters features.

### Controls

- provider abstraction;
- provider/source version where available;
- local provenance and verification state;
- deterministic normalization;
- regression fixtures;
- changes to high-impact input semantics require reevaluation.

## 16. Threat: AI-generated feature contamination

### Failure mode

LLM/Rina output is used as if it were observed mechanical fact.

### Controls

- raw AI text is not an authoritative feature or label;
- structured clinical summaries retain provenance and review state;
- no chain-of-thought storage or training dependency;
- if AI-derived classifications are ever used, they require a separately evaluated contract and must remain distinguishable from human-reviewed data.

## 17. Threat: prompt injection or malicious evidence

### Failure mode

Uploaded text/images or client messages attempt to manipulate an AI extractor or future explanation layer into changing authority, exposing records or generating unsupported conclusions.

### Controls

- Wave 1.3 authority policy executes outside provider text;
- extraction/provider output cannot expand permissions;
- raw media remains evidence until professional review;
- retrieved context is vehicle-scoped and minimized;
- no provider-generated action executes directly;
- suspicious/untrusted extraction remains labelled as such.

## 18. Threat: privacy leakage through explanations

### Failure mode

A prediction explanation reveals another client/vehicle, advisor-only note, raw evidence metadata or unrelated personal information.

### Controls

- explanation constructed from visibility-safe supporting record IDs;
- role-aware projection after prediction, not before authority resolution;
- no nearest-neighbour/example disclosure from another customer's raw record;
- no storage keys, hashes, prompts or private provider payloads in client output.

## 19. Threat: discriminatory or irrelevant personal features

### Failure mode

Mechanical-risk output changes because of customer demographics, occupation, wealth proxies, emotional state or communication behaviour.

### Controls

- prohibited-feature list in the prediction contract;
- feature inventory review;
- subgroup analysis only where lawful and statistically meaningful;
- mechanical/operational features must have a defensible relationship to the target.

## 20. Threat: data-retention mismatch

### Failure mode

Records retained for care continuity are copied into an indefinite training dataset after their original retention/deletion context changes.

### Controls

- predictive datasets remain linked to source governance;
- deletion/correction policy propagates to derived datasets where required;
- no unmanaged CSV/training dump as a shadow system;
- retention purpose reviewed before training begins.

## 21. Threat: model/version ambiguity

### Failure mode

Aura cannot reproduce which logic produced a historical prediction.

### Controls

Any future prediction record must identify:

```text
prediction_contract_version
feature_version
rules_or_model_version
provider/source versions where material
as_of_time
supporting record IDs
```

Unversioned predictive output is not production-admissible.

## 22. Threat: alert fatigue

### Failure mode

Repeated low-value risk notifications cause advisors/clients to ignore important signals.

### Controls

- predictions initially advisor-only;
- deduplication/cooldown rules designed per target;
- surface change in evidence rather than repeated identical warning;
- measure actionability and advisor burden during shadow mode.

## 23. Threat: silent runtime failure

### Failure mode

Feature generation/model/provider fails and Aura substitutes guessed output or stale cached certainty.

### Controls

- fail toward abstention;
- explicit provider/model failure state;
- no silent fallback to a different semantic target;
- operational metrics and audit events;
- feature flag / rollback control before any client-facing use.

## 24. Threat: unsupported product marketing

### Failure mode

Commercial copy outruns validated capability and describes Aura as predicting failures or diagnosing faults.

### Controls

- predictive-health claims remain prohibited until approved evaluation and client-safe contract exist;
- product declaration remains controlling;
- marketing language must distinguish monitoring/pattern detection from diagnosis/prediction certainty.

## 25. Readiness severity classification

Readiness findings should be classified:

```text
blocking
material
monitor
```

Examples of **blocking** findings:

- no trustworthy labels for the target;
- severe time leakage;
- cross-vehicle privacy flaw;
- outcome data too sparse to evaluate;
- no reproducible baseline;
- no abstention path;
- unacceptable false-negative safety risk;
- ungoverned training-data retention.

Any blocking finding forces:

```text
collect_more_data
```

or:

```text
defer
```

## 26. Release boundary

No predictive capability may move beyond readiness into implementation until:

- the data-readiness report is reviewed;
- the exact target contract is approved;
- blocking threats are closed or explicitly make the decision `defer`;
- evaluation and shadow-mode plans exist;
- rollback ownership is assigned.

The opening Wave 1.5 architecture PR intentionally adds no prediction runtime.
