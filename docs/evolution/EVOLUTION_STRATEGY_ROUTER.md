# Evolution Strategy Router

This router tells Codex which evolution strategy spec to read before writing an evolution proposal.

Python may load this file, list candidate specs, archive evidence paths, validate proposal shape, and apply rollbackable changes. Python must not decide the business strategy, choose which browser evidence is important, or hard-code website behavior.

## Routing Rule

1. Identify the primary evolution target from the action card, run context, candidate id, site phase, and user request.
2. Read the primary candidate spec under `docs/evolution/candidates/`.
3. If the target belongs to the site workflow family, also read the related family specs listed below.
4. Use the evidence index to choose which local files to inspect. Do not assume Python-selected excerpts are exhaustive.
5. Write a concrete proposal through the existing proposal schema. Do not answer with only a summary.

## Review-Gated Triggering

When a site reaches an evolution threshold, CareerEng may create a Codex-readable review card before creating a concrete evolution run.

The review card is not the proposal. It should be used by Codex to explain the trigger, list available evolution directions, and ask the user whether to evolve now. If the user agrees, Codex should select one or more existing candidate specs and then create the normal solution request/proposal through the existing evolution flow.

The review card may list related accepted lessons, memory units, and action cards from other sites. These are evidence candidates only. Codex/LLM decides whether to inspect them and whether any cross-site lesson should influence the current site. Python must not auto-transfer lessons or decide applicability.

If the user says not now, record the skip/selection outcome so the same threshold evidence does not repeatedly interrupt the user until new evidence reaches the next threshold.

## Strategy Map

| Strategy | Candidate Spec | Use When |
| --- | --- | --- |
| `site_workflow_evolution` | `new_site_workflow_transfer` | A new target company/site needs a draft or first testable site Skill. |
| `site_workflow_evolution` | `apply_form_workflow` | A selected job enters apply/form/upload/submit workflow and repeated blockers or probe results should change strategy. |
| `site_workflow_evolution` | `site_workflow_compaction` | An existing site/phase has repeated runs and should compact stable operations, caveats, or workflow memory into Skills. |
| `application_strategy_evolution` | `application_strategy_evolution` | JD matching, filtered-out policy, user/site matching-policy calibration, application decision rules, or outcome-driven strategy should change. |
| `assistant_memory_evolution` | `assistant_router_memory_intake` | Codex/@career conversations, memory routing, curation examples, or assistant-intake policy should improve. |
| `resume_profile_evolution` | `resume_profile_evolution` | CV, persona, profile facts, resume Markdown/PDF, or user capability representation should change. |
| `target_company_intelligence_evolution` | `target_company_intelligence_evolution` | Company demand, rejected/progress patterns, skill gaps, learning plans, or interview intelligence should be synthesized. |

## Site Workflow Family

The following specs are one family but remain separate files:

- `new_site_workflow_transfer`
- `apply_form_workflow`
- `site_workflow_compaction`

Use the family relationship to transfer lessons across site bootstrap, retrieval/filter/status workflow, and apply-form workflow. Do not merge their storage or Python paths merely because they are related.

## Evidence Selection Policy

Python should provide an evidence index with paths, counts, and trace references. Codex/LLM decides what to inspect based on the strategy spec.

Allowed Python responsibilities:

- list candidate specs and router path
- list evidence files, trace refs, failure snapshots, workflow summaries, action cards, memory units, lessons, and Skill paths
- include small starter excerpts for convenience
- record engineering signals such as empty snapshot after retries
- validate proposal schema and apply supported changes

Disallowed Python responsibilities:

- decide which business evidence proves the diagnosis
- choose site-specific form actions, selectors, filters, or matching policy
- infer user facts or job desirability
- write narrow site-specific runtime branches to make one observed case pass

## Matching Policy Calibration Routing

Route to `application_strategy_evolution` when the primary question is whether user/application evidence should change project-level or site-level matching rules.

Examples:

- the user corrects a `filtered_out` role and says it should have been considered
- a company has repeated positive-progress roles with a shared JD pattern
- a company has repeated matching-policy filtered-out rows that may no longer reflect the user's current direction
- imported application feedback says the user's target direction has shifted

Do not route these cases to `site_workflow_evolution` unless the issue is how to operate the website. Website navigation, filters, forms, uploads, and submit flow are workflow evolution. Job desirability, role-cluster weighting, and `Matching Policy` wording are application-strategy evolution.

## Proposal Expectations

Every proposal should state:

- selected primary strategy and candidate spec
- evidence files actually inspected
- diagnosis grounded in that evidence
- concrete change type such as `run_local_overlay`, `skill_patch`, `memory_unit_append`, `routing_example_append`, or `assistant_context_update`
- validation plan for the next unit/run
- whether the result should remain run-local or be promoted to durable Skill/memory/lesson

## Run-Local Versus Durable Output

Codex must match the proposal type to the loop level.

- Inner-loop proposals may use `run_local_overlay` to change the next item inside the current run or batch.
- Outer-loop or batch-synthesis proposals use the same proposal schema and should choose supported change types from the evidence and candidate spec.
- Durable changes such as `skill_patch`, `memory_unit_append`, `routing_example_append`, accepted lesson/action-card updates, or `assistant_context_update` should be used when the evidence supports a durable strategy update.
- If the evidence only supports a short-horizon experiment, Codex may propose `run_local_overlay`, but the next batch must validate whether it actually changes outcomes.
- A batch-level synthesis is accepted only through the existing report/selection/capability flow after follow-up evidence supports it.

If evidence is insufficient, propose a better evidence-gathering step or keep observing. Do not invent facts.
