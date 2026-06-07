# Evolution Run Protocol

CareerEng evolution is a repeatable run protocol, not a one-off patch workflow.

Every evolution candidate should follow the same lifecycle:

1. `trigger`
2. `probe/run`
3. `evidence_pack`
4. `report`
5. `next_action`
6. `linked_followup_run`

This applies to new-site workflow transfer, site apply workflows, assistant-memory routing, skill compaction, target-company intelligence, and future evolution candidates.

## Boundaries

Python is responsible for:

- collecting local evidence
- enforcing budgets
- stopping unsafe or wasteful loops
- writing structured reports
- preserving rollback and follow-up metadata

The LLM is responsible for:

- interpreting evidence
- deciding whether the issue is missing user facts, site-skill behavior, project-skill behavior, runtime/page behavior, or normal rejection
- proposing patches or next actions

Skills are responsible for:

- durable human/LLM-readable workflow behavior
- site-specific or project-level instructions once a proposal is accepted

Python should not encode site-specific click paths, selectors, or ATS-specific application behavior.

## Required Run Fields

Each evolution run should identify:

- `candidate`: the evolution candidate id
- `scope`: site, phase, capability, or memory/router target
- `budget`: max attempts, max failures, max time, max tokens, or candidate-specific limits
- `success_criteria`: what counts as a successful probe
- `failure_criteria`: what counts as a failed probe
- `evidence`: traces, local state, visible fields, run counters, and relevant skill/profile facts
- `report`: human-readable and machine-readable outcome
- `next_action`: what should happen after this run

## Report Contract

Every completed evolution run must write:

- `workspace/evolution/runs/<run_id>/report.json`
- `workspace/evolution/runs/<run_id>/report.md`

Failure is not silent. If the run stops because it hit a budget, repeated blocker, missing fact, or runtime issue, it still writes a report.

## Next Actions

Reports should classify the next action as one of:

- `ask_user_fact`: the local profile is missing a required fact
- `propose_site_skill_patch`: the site skill needs clearer site-specific behavior
- `propose_project_skill_patch`: the project skill needs a general rule
- `inspect_runtime_or_page`: the page/runtime appears stuck or unstable
- `evaluate_acceptance`: the probe completed and should be evaluated for acceptance
- `retry`: rerun after user action or after a proposal is applied

## Linked Follow-Up Runs

If a report asks for user facts or proposes a skill patch, the next run should link back to the previous report.

The follow-up run should include:

- previous `run_id`
- previous `report.json`
- new user-provided facts or accepted patch
- reason for retry

This prevents the system from rediscovering the same failure repeatedly.
