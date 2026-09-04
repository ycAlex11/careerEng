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

## Two-Layer Evolution Model

CareerEng separates short-term loop learning from long-term evolution.

### Short-Term Run/Batch Loop

The short-term layer is scoped to one current run or batch. It is allowed to make
the next loop item smarter, but it must not treat one observation as a durable
truth.

Python is responsible for:

- recording loop evidence
- creating/updating an executable action card
- writing run-local workflow evidence and solution requests
- injecting only concrete proposals into the next iteration context
- enforcing thresholds and human-only boundaries

The LLM is responsible for:

- producing a concrete proposal before a failed strategy is retried
- using that proposal on the next item
- completing the item to a terminal state or writing a structured loop-control gap
- avoiding the known failed strategy

Evidence, action cards, generic refinement hints, and failure summaries are not
proposals. A failed workflow may continue only after a concrete proposal exists,
such as `run_local_overlay`, `skill_patch`, `routing_example_append`,
`memory_unit_append`, or `assistant_context_update`.

The short-term layer may continue the batch until the configured threshold is
reached. It should not directly create accepted lessons.

When the batch ends, short-term proposals must be summarized and closed or
archived for synthesis. They must not leak into later batches as active
strategy unless a later long-term proposal promotes them into durable Skill,
memory, routing, or context changes.

### Long-Term Evolution / Lesson Layer

The long-term layer runs after a batch or after enough accumulated evidence.

It is responsible for:

- summarizing repeated loop patterns across the batch/run window
- creating lesson candidates, open candidates, and action cards
- proposing site skill, project skill, profile/memory, or config changes
- evaluating follow-up runs before accepting or rolling back a change

Accepted lessons and durable skill changes belong here, not inside a single
apply item.

Outer-loop synthesis must produce a concrete proposal using the existing
proposal schema. Codex/LLM chooses the supported change types from the evidence
and candidate spec. Python does not decide whether the answer should be a skill
patch, memory lesson, routing update, context update, run-local experiment, user
fact request, or another supported proposal shape.

After the proposal is applied, the next outer batch validates it. If the batch
has no failures, the existing report/selection/capability flow can accept the
evolution. If failures remain and the outer budget is not exhausted, another
synthesis cycle may run.

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

## Skill Section Ownership

Evolution proposals should target the narrowest stable skill section:

- Posted-window rules, retrieval date windows, and historical-area stop policy belong in `## Site Policy`.
- JD/persona/CV matching rules, site-native match labels, hard role exclusions, and application gates belong in `## Matching Policy`.
- Search surface navigation belongs in `## Channel Discovery`.
- Filter selection and filter completion criteria belong in `## Job Filtering`.
- Result extraction, pagination, and retrieval stop mechanics belong in `## Job Retrieval`.
- Form filling, upload behavior, submit criteria, already-applied signals, and apply recovery belong in `## Apply`.

Do not propose Python code patches for site-behavior learning unless the evidence shows a framework/runtime bug. Site behavior should first be corrected through skill sections.

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

## Loop Engineering Control

Loop-control signals are not all user pauses.

### Evolution Decision Contract

At the end of a batch, orchestration may build a lightweight
`evolution_decision` from the LLM-provided loop-control evidence.

The decision is intentionally thin:

- `verdict`: `continue_evolution`, `needs_solution_proposal`, `needs_user_input`, or `stop_no_action`
- `site_key`, `phase`, `failure_pattern`, and source evidence/action-card refs
- `target_ref` and `refinement_hint` supplied by the LLM/Skill
- `proposal_overlay`: temporary run context only when backed by a concrete proposal
- `validation_plan`: what the next batch should prove

Python must not infer detailed business categories here. If the LLM/Skill says
there is a refinement opportunity but no concrete proposal has been produced,
the runner must pause at `waiting_evolution_solution` and surface the action
card/evidence pack to a solution provider such as Codex or Claude Code. The
outer loop can run the next batch only after a concrete proposal exists.

Default behavior:

- `trigger_refinement` means the current loop found a reusable skill/profile/workflow gap. Record evidence and create/update the candidate/action card. Continue only if a concrete `run_local_overlay` or durable proposal exists; otherwise pause for a solution provider.
- `request_user_input` means the system appears to lack a real user fact. Record evidence and continue observing until the configured user-input threshold is reached, unless the gap is clearly human-only.
- `retry_recovery` means the runtime/page state likely needs recovery, not skill editing.
- `pause_site` and `pause_batch` are explicit stop signals.

At inner-loop level, a concrete `run_local_overlay` is sufficient to try the
next item in the same batch. At outer-loop level, the existing proposal/apply
flow controls continuation: apply the proposal, close batch-local overlays as
evidence, and run the next outer batch if the configured outer budget allows it.
Do not treat old batch-local overlays as durable site strategy unless a proposal
or accepted lesson explicitly promotes them.

Immediate human boundaries:

- login password entry
- MFA
- CAPTCHA
- email or device verification
- account safety or other human-only gate

Threshold behavior:

- Same reusable refinement gap may continue within the current loop until the configured per-batch threshold is reached.
- Same missing-user-fact gap may continue until the configured user-input threshold is reached.
- After threshold exhaustion, pause and surface the latest evidence/action card instead of silently repeating stale behavior.

Python should enforce the loop mechanics and thresholds. The LLM/Codex should diagnose the gap and propose the smallest skill/profile/config change.

Business batches do not wait for this protocol. Evolution requests are durable
side work delivered to the registered main agent. Exploration readiness review
becomes due after three version-scoped consecutive successful full cycles;
ready-site scheduled evolution becomes due after five new effective full runs.
Confirmed internal defects may enter the same flow early. External transient
failures are recovery evidence, not evolution triggers, and unknown-origin
failures remain observations until diagnosed.

Routine rollbackable proposals may proceed without a separate user-confirmation
step. They still require a concrete proposal, a pre-apply snapshot, focused
validation, next-run activation, and rollback on failed validation. Login,
CAPTCHA, final-submit safety, account data, and other irreversible boundaries
remain human-controlled.

At batch end, write a workflow evolution summary. The summary can create lesson
candidates or follow-up candidates, but it must not auto-accept durable lessons
from one batch alone.

The batch-end summary should include run-local proposal usage and validation
outcomes so Codex can decide whether to promote, revise, keep observing, or
discard them. Python should not make that business decision; it should only
make the lifecycle state explicit.

## Linked Follow-Up Runs

If a report asks for user facts or proposes a skill patch, the next run should link back to the previous report.

The follow-up run should include:

- previous `run_id`
- previous `report.json`
- new user-provided facts or accepted patch
- reason for retry

This prevents the system from rediscovering the same failure repeatedly.
