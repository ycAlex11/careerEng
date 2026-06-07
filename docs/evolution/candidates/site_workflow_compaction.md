---
id: site_workflow_compaction
name: Site Workflow Compaction And Pattern Learning
target_type: site_skill_section
target_ref: skills/search/jobs/sites/<site>/SKILL.md
risk_level: medium
apply_policy: auto_draft_human_review_for_apply_behavior
---

# Candidate: Site Workflow Compaction

## Purpose

Compact repeated website operation experience into reusable site skill guidance and workflow pattern memory.

This candidate is not only about reducing time or token usage. Its main value is to help CareerEng learn reusable career-site operation patterns so that:

- existing site skills become more stable
- repeated successful flows are written down
- repeated failures become explicit caveats
- new site skills can be drafted from known patterns
- the LLM does not rediscover the same page behavior from scratch every run

## Required Evidence

Use these local sources:

- `workspace/jobs/batches/*.json`
- `workspace/evolution/browser_control/phase_events.jsonl`
- `workspace/sites/<site>/evolution/workflow_memory.json`
- `workspace/sites/<site>/evolution/failure_snapshots/*.md`
- `workspace/metrics/llm_usage.jsonl`
- `workspace/sites/<site>/jobs/runs/*.jsonl`
- `workspace/sites/<site>/applications/reviews/*.jsonl`
- `workspace/reports/jobs/`
- current site skill
- project jobs skill

Useful evidence includes:

- repeated successful phase completions
- no-progress guards
- empty extraction loops
- ignored stop/enrichment events
- repeated page/action patterns
- user intervention points
- unmatched review records after review phases
- phase duration and usage trends
- site-specific carry-forward memory that repeatedly worked
- failure snapshots that show the live page state when a phase failed

## Allowed Proposals

Default boundary:

- If a workflow fails because the LLM chose the wrong site-specific operation, filter option, selector path, page route, or stop condition, refine the site skill or workflow memory first.
- Do not propose Python runtime changes before exhausting site skill / workflow memory refinement for site-specific behavior.
- Runtime changes are reserved for generic cross-site infrastructure problems such as protocol handling, timeout accounting, persistence, safety guards, or tool transport failures.

Inference requirement:

- Use traces, failure snapshots, workflow memory, and successful runs to infer missing site-specific workflow steps.
- Do not only summarize that a phase failed. Identify the concrete missing or wrong website operation when evidence supports it.
- If the live page exposes stable options that map to the project goal, such as role family, team, location, sorting, application status tabs, or apply-state labels, propose explicit site skill steps for those options.
- Prefer a concrete site skill patch that tells the LLM what to do next time, including entry point, exact site-visible labels, stop condition, and what not to repeat.
- If evidence is insufficient to infer a stable step, write a workflow-memory caveat and keep observing instead of inventing a brittle rule.

The LLM may propose:

- rewriting a site skill section for clarity
- compacting stable site workflow steps into the site skill
- adding caveats about repeated failure modes
- updating workflow memory when evidence is not stable enough for the site skill
- extracting a reusable workflow pattern for future site skill drafting
- suggesting a project-level generalization when the same pattern appears across multiple sites
- suggesting evidence that a workflow should keep observing instead of changing

The LLM must not propose:

- adding Python browser-action semantics
- hard-coding selectors into Python
- changing Python runtime just because one site skill under-specifies a page-specific workflow
- changing provider, MCP, or browser protocol behavior
- changing login security, MFA, CAPTCHA, or account-safety handling
- weakening final-submit safety

## Evaluators

Deterministic evaluators:

- phase success rate
- recent 10-run phase duration, especially `job_retrieval`
- recent 10-run LLM elapsed time, call count, and token usage
- no-progress guard count
- empty extraction count
- pagination policy violation count
- repeated page/action count when available
- unmatched review count
- blocked or user-takeover count
- retrieved job count stability
- JD, URL, and site job id completeness when retrieval is in scope
- submitted job count when apply is in scope

LLM-assisted evaluators:

- whether the workflow pattern is truly reusable
- whether the site skill section became clearer
- whether a site-specific pattern should stay local or move to project-level guidance
- whether a new-site skill could reuse the pattern

Human evaluators:

- user confirms the site behavior matches visible browser reality
- user accepts or rejects site skill workflow changes
- user confirms when a pattern should be generalized

## Metric Contract

Python evaluators compute metrics from local runtime records. The LLM may reference these metrics when drafting proposals, but it must not invent metric values.

For retrieval/workflow changes, compare the latest evidence before and after apply:

- `phase_duration`: recent average and median when available.
- `llm_cost`: calls, elapsed time, input tokens, output tokens, total tokens, and unknown-token calls.
- `guard_events`: no-progress, same-url no-progress, empty extraction, stop-policy, and enrichment-policy guards.
- `retrieval_quality`: retrieved count stability, missing URL/JD/site-job-id count, and unmatched review count.
- `runtime_quality`: blocked/failed site runs, user takeover count, and repeated same-page actions.

Selection should stay conservative:

- If follow-up evidence is insufficient, select `keep_observing`.
- If quality gets worse, select `rollback_recommended`.
- If quality is stable or better and runtime/cost improves, select `accepted`.
- Cost improvement must not override worse application quality or worse data completeness.

## Trigger Contract

Trigger workflow evolution by `site_key + phase`, not by whole-site count.

Scheduled trigger:

- Count terminal phase runs for each `site_key + phase`.
- Terminal phase runs include `done`, `blocked`, and `failed` because all three provide workflow evidence.
- When `current_phase_run_count - last_evolved_phase_run_count >= 10`, create a `site_workflow_compaction` evolution run scoped to that site and phase.
- Do not mix phase buckets. A site that ran status review 10 times and apply 5 times should only trigger status-review evolution.

Problem-driven trigger:

- Trigger early when the same `site_key + phase` repeatedly shows runtime friction.
- Use browser-control events such as no-progress guards, empty extraction loops, stop-policy violations, enrichment-policy violations, repeated same-page behavior, blocked phases, and failed phases.
- Prefer early trigger when repeated friction appears in the recent window even if the scheduled 10-run threshold has not been reached.

Apply and evaluation window:

- `site_workflow_compaction` proposals may be auto-applied only when they are rollbackable site skill section patches or append-only workflow memories allowed by the proposal schema.
- The apply step must snapshot rollbackable targets before writing.
- After apply, evaluate only the next 3 runs for the same `site_key + phase` bucket.
- If those follow-up runs fail quality gates, rollback should be recommended or performed through the rollback path.

Quality gates before scoring:

- Phase success must not degrade into repeated `blocked` or `failed`.
- Retrieval job count must not drop sharply for retrieval changes.
- URL, JD, and site job id completeness must not get worse when retrieval is in scope.
- Unmatched review count must not increase for status-review changes.
- The proposal must not violate the browser automation boundary.

Scoring after quality gates:

- Runtime time: 40%.
- Token cost: 20%.
- Repeated attempts and same-page exploration: 20%.
- Guard and error reduction: 15%.
- Data completeness: 5%.

## Archive Requirements

Archive each evolution run with:

- current site skill section
- baseline phase metrics
- browser-control evidence
- proposed site skill patch
- proposed workflow pattern memory
- affected phases
- expected evaluator changes
- after-run metrics when available
- rollback snapshot if a patch is applied

## Output Contract

A proposal should include:

- `site_skill_patch_proposal`
- `inferred_missing_workflow_steps`
- `workflow_pattern_summary`
- `site_variations`
- `failure_caveats`
- `project_skill_generalization_candidate`
- `new_site_drafting_notes`
- `evaluation_plan`
- `risk_notes`

## Selection Criteria

Prefer proposals that:

- improve stability without creating brittle browser action rules
- keep website-specific behavior in site skills
- move only proven cross-site patterns toward project-level guidance
- help future new-site skill drafting
- preserve official browser/MCP tool boundaries
- make rollback possible if the workflow becomes slower or less reliable
