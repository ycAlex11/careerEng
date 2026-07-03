---
id: apply_form_workflow
name: Apply Form Workflow
target_type: site_apply_workflow
target_ref: skills/search/jobs/sites/<site>/SKILL.md
risk_level: medium
apply_policy: auto_probe_report_human_or_llm_review
---

# Candidate: Apply Form Workflow

## Purpose

Improve the application-form phase for a registered site by running bounded probes, collecting repeated blockers, and producing an evolution report.

This candidate starts after jobs have already been retrieved and filtered. It does not decide which jobs are desirable. It only evaluates whether the site's apply workflow can reliably move from a selected job detail page to a terminal outcome.

## Evolution Strategy

This is a site-workflow evolution strategy focused on the apply/form phase.

Loop shape:

- Inner loop: each selected apply target is one validation unit. If one unit exposes a reusable failure pattern, Codex may generate a `run_local_overlay` for the next unit instead of waiting for the whole batch.
- Outer loop: when the apply probe or batch completes, summarize all inner-loop proposals, validations, blockers, and traces. Promote stable changes to `skill_patch`, accepted lesson, memory, or action card when evidence supports durability.
- Stop loop: pause only for human-only blockers, missing user facts, or repeated same-pattern failures beyond the configured threshold.

Run-local and outer-loop outputs have different lifecycles:

- `run_local_overlay` is valid only for the current batch or current short-horizon validation unit.
- At batch end, all active run-local overlays should be summarized with their usage and validation results, then closed or archived for synthesis.
- The next outer batch should validate a durable change, not an old batch-local overlay.
- If Codex cannot justify a durable `skill_patch`, memory/lesson update, routing/context update, or explicit keep-observing decision from the evidence, the outer synthesis is incomplete and should not be treated as successful evolution.

Codex intervention points:

- after a sampled form workflow fails without a terminal state
- after repeated blockers point to a site-skill or project-skill gap
- after upload/form/continue/submit evidence shows unstable page observation
- after probe completion, whether successful, failed, or partial

Evidence-selection policy:

- Start from the evolution strategy router and this spec.
- Inspect the evidence index and choose the relevant run rows, trace refs, failure snapshots, active run-local proposals, workflow summaries, and Skill sections.
- For outer-loop synthesis, inspect the inner-loop proposal usage and validation outcomes, not only the last failure example.
- For upload or page-render instability, inspect before/after tool traces and snapshots around the browser operation. Treat empty snapshot after retries as engineering evidence, not as a site-specific form rule.
- For form-field strategy, compare repeated successful submissions, failed validation traces, blockers, and snapshots to infer the site's minimal required submit path. Do not assume every visible field is required. Treat a field as optional unless the live page blocks submission, shows validation, the active Skill marks it required, or the user explicitly requires it.
- When multiple successful submissions show that optional fields can be skipped safely, summarize that evidence and propose a site Skill refinement only after the pattern is stable enough to reuse.
- Do not rely only on Python-provided excerpts. They are starter context, not the full evidence set.
- Do not let Python choose form strategy, field mappings, or job desirability.

## Probe Budget

V1 budget:

- `max_apply_probe_form_samples`: 8
- `stop_unsuccessful_threshold`: 5

Stop the current apply probe when either condition is reached.

## Counting Rules

`form_sampled` means a job actually exercised the site's apply-form workflow.

Examples:

- uploaded or selected a resume
- reached an application form step
- filled, selected, validated, reviewed, continued, or submitted required application fields
- reached a final submitted confirmation state after form flow

`apply_path_attempted` is informational. It may include a job where the system clicked an apply entry or found an already-applied state, but it does not by itself count toward the 8 form samples.

`form_unsuccessful` means a sampled form workflow did not reach a successful terminal state.

Examples:

- `blocked_form_validation`
- `missing_profile_fact`
- `apply_failed` after upload, form fill, validation, continue, review, or submit workflow evidence
- `blocked` after reusable profile/application fields could not be completed

These do not count toward the form-sample budget:

- `filtered_out`
- `already_applied`
- `closed`, `withdrawn`, `rejected`, or unavailable jobs
- password, MFA, CAPTCHA, verification code, passkey, or device-approval blockers before the form workflow
- opening a JD and deciding not to apply

If the LLM opens a JD, compares it to persona/CV, and decides not to apply, that is normal matching behavior and should not count against apply workflow reliability.

Successful form-sample terminal states include:

- `submitted`

### Excluded-Role Hard Gate

Intern/new-grad/campus roles are never valid apply-probe samples.

If a live title, card, or JD shows `intern`, `internship`, `campus`, `student`, `new grad`, `new graduate`, `co-op`, `校招`, or `实习`, the job must be recorded as `filtered_out` before entering the apply workflow.

If an excluded role reaches apply workflow, the probe report must mark an excluded-role violation and must not auto-accept the site apply workflow.

## Blocker Categories

Classify blockers into:

- `missing_profile_fact`: the page requires a fact not present in local profile/apply facts
- `human_auth_required`: password, MFA, CAPTCHA, verification code, passkey approval, or device approval
- `site_skill_candidate`: local facts exist, but the site skill does not explain how to map them to this site's fields
- `project_skill_candidate`: the rule is general across sites and belongs in the project jobs skill
- `runtime_or_page_issue`: page loading, stale refs, repeated no-progress actions, or browser/runtime instability

## Required Evidence

The report should include:

- site key and phase
- batch id
- form-sampled count
- form-successful count
- form-unsuccessful count
- apply-path-attempted count
- filtered-out count
- excluded-role violation count
- repeated blocker summaries
- visible required fields or validation text when available
- available `apply_facts` summary
- missing local fact paths if facts are absent
- proposed next action

## Runtime Boundary

Python may:

- count form-sampled and form-unsuccessful jobs
- enforce the 8/5 probe budget
- generate the report
- stop the current apply probe
- persist an accepted apply capability after the report satisfies auto-acceptance gates

Python must not:

- hard-code site-specific field mappings
- write site-specific browser actions
- infer private facts not present in the profile
- silently continue through repeated blockers
- auto-accept a probe that has excluded-role violations or missing reusable profile facts

LLM/Codex may:

- inspect the report and evidence
- decide whether the next step is user fact collection, site skill patch, project skill patch, or runtime investigation
- propose skill updates after evidence is available
- review or override the accepted capability later if user feedback shows the workflow was wrongly accepted
- decide whether inner-loop run-local behavior should be promoted, revised, kept observing, or discarded at batch end

## Completion Standard

An apply probe is complete when:

- it reaches the form-sample budget
- it reaches the unsuccessful threshold
- all pending apply rows reach terminal states before either budget is reached

Every completion path must generate a report when the probe budget stops the run.

Auto-acceptance is allowed only when:

- `form_sampled >= max_apply_probe_form_samples`
- `form_unsuccessful < stop_unsuccessful_threshold`
- no excluded-role violation exists
- no required reusable profile fact is missing

When auto-accepted, future runs for that site's `apply_form_workflow` should run full apply instead of stopping after the 8-sample probe. The report should still explain whether the next step is a site-skill refinement, profile update, runtime investigation, or acceptance.
