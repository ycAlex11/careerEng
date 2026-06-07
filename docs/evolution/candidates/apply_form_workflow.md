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
