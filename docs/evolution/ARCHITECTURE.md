# CareerEng Evolution Architecture

CareerEng evolution is a learning loop, not a single trigger, rule, or optimization pass.

The goal is to make CareerEng progressively better at understanding the user, understanding employer/job-market expectations, and bridging the gap between them while keeping local browser execution reliable and inspectable.

## Purpose

CareerEng should evolve toward four engineering outcomes:

- Better user understanding: skills, constraints, goals, preferences, projects, learning progress, and interview readiness.
- Better employer understanding: recurring job requirements, company-specific signals, application lifecycle signals, and hiring feedback.
- Better gap bridging: clearer application strategy, resume/project improvement direction, learning plans, and interview preparation.
- Better execution: more reliable site workflows, cleaner data, better routing, and less repeated exploration.

Execution improvements matter because they produce better evidence. They are not the final product goal by themselves.

## Framework View

CareerEng evolution should be implemented as a framework, not as a collection of one-off rules.

The framework is inspired by the same high-level pattern used by systems such as AlphaEvolve:

```text
Candidate
-> Evaluator
-> Archive
-> Proposal / Mutation
-> Selection
```

CareerEng should adapt this pattern to job-search intelligence instead of algorithm discovery.

In CareerEng:

- Candidate: an object that can be improved, such as a skill section, routing examples, memory consolidation behavior, report summary, data repair plan, or application matching strategy.
- Evaluator: a deterministic, LLM-assisted, or human signal that says whether a candidate is better.
- Archive: the local history of candidates, proposals, versions, outcomes, accepted changes, rejected changes, and rollback information.
- Proposal / Mutation: an LLM-generated change based on evidence and current candidates.
- Selection: the decision to accept, reject, rollback, or keep observing a proposal based on evaluation evidence.

Triggering an evolution run is only the entry point. The actual intelligence comes from candidate design, evaluator quality, archive quality, and LLM proposal quality.

## Core Loop

The evolution loop is:

```text
Experience
-> Distillation
-> Hypothesis
-> Change
-> Evaluation
-> Retention
```

Each step has a separate responsibility.

## Experience

Experience is the raw material collected during normal use.

Examples:

- User and assistant conversations about job search, resume, goals, learning, and interviews.
- Persona, intent, CV, and resume variants.
- Job descriptions and website-visible job facts.
- LLM apply decisions, including why a job was submitted, skipped, filtered, blocked, or already applied.
- Application outcomes such as rejected, active, in process, interview, assessment, offer, withdrawn, or closed.
- Browser workflow traces, phase events, no-progress guards, enrichment issues, and site-specific failures.
- Assistant routing events, user corrections, and explicit `@career` examples.
- Metrics such as runtime, phase duration, token usage when available, failed calls, and batch outcomes.

Experience should be durable and local-first. Raw records are allowed to be verbose because they are evidence, not final memory.

## Distillation

Distillation compresses raw experience into reusable knowledge.

CareerEng should not turn every message or log into long-term memory. It should keep raw history searchable, then promote only useful conclusions into structured memory or improvement candidates.

Important distillation outputs:

- User model: durable facts about the user's capabilities, constraints, goals, preferences, projects, and learning progress.
- Employer/job requirement model: recurring requirements observed across JD text, companies, roles, and application outcomes.
- Gap model: differences between the user's current profile and target roles or companies.
- Application strategy model: what kinds of JD signals correlate with positive outcomes, fast rejections, blocked applications, or interview progress.
- Workflow pattern model: reusable site-operation patterns learned from repeated successful or failed website flows.
- Router examples: interaction samples that teach the assistant when a message should enter CareerEng and which local command or memory category it maps to.
- Execution evidence: structured facts about runtime failures, repeated loops, data quality issues, and unstable workflows.

This mirrors the useful part of systems such as Hermes/OpenClaw: keep bounded curated memory, keep raw sessions searchable, and promote recurring workflows or durable facts only when they are useful.

## Hypothesis

Hypothesis is where the LLM should do most of the reasoning.

Given distilled evidence, the LLM can propose explanations such as:

- The current common JD matching rule may be approving too many frontend-heavy jobs.
- AI infrastructure roles that mention platform, distributed systems, evaluation, or GPU tooling appear more aligned with the user.
- A site workflow is stable enough to be compacted into the site skill.
- A pattern used by multiple sites should be moved from site skill guidance into project-level skill guidance.
- Some user conversations should be saved to CareerEng memory even when they do not include `@career`.
- A specific data quality issue should be repaired before application summaries can be trusted.

The code should not try to hard-code all business interpretations. The code should prepare reliable evidence packs and enforce boundaries. The LLM should interpret, diagnose, and draft changes.

## Change

A change is a controlled modification proposed by the evolution loop.

Possible change targets:

- Project job skill, especially common apply matching, retrieval, filtering, review, or recording rules.
- Site skill, especially site-specific login, dashboard review, retrieval, apply, resume upload, and form-fill workflows.
- Assistant routing examples or routing rules.
- Memory units, persona notes, intent notes, application strategy notes, or interview preparation notes.
- Summary/report behavior and derived statistics.
- Data repair or enrichment plans.
- Runtime configuration such as budgets, timeouts, or site parallelism.

The preferred order is:

```text
memory / data repair / router examples
-> skill changes
-> reporting / summary changes
-> runtime config
-> Python runtime code only when evidence points to host-layer failure
```

## Candidate

An evolution candidate is a concrete thing that can be improved and evaluated.

Examples:

- `skills/search/jobs/SKILL.md` `Apply -> Matching`: improve how the LLM decides whether to apply to a visible JD.
- `skills/search/jobs/sites/qualcomm/SKILL.md` `Application Status Review`: improve how the agent reads dashboard statuses.
- Assistant bridge routing examples: improve when Codex messages should enter CareerEng memory.
- Application summary rules: improve what the summary exposes about rejection speed, in-process roles, or unmatched records.
- Memory consolidation prompt/spec: improve how raw career conversations become durable memory units.
- New-site skill drafting support: use previously learned workflow patterns when adding a new company site.

Candidates should be easy to add. A new candidate should not require hard-coding a new Python branch whenever possible.

The long-term shape should be spec-driven:

```text
candidate spec
-> evidence requirements
-> allowed change type
-> evaluator
-> archive result
```

The spec can be Markdown or YAML/JSON front matter plus Markdown. Python should provide the framework that loads specs, builds evidence packs, records runs, and calls evaluators.

## Evaluator

Every candidate needs an evaluator. Without an evaluator, the system is only generating suggestions, not evolving.

Evaluator types:

- Deterministic evaluator: Python-computed metrics such as phase duration, guard count, unmatched count, rejected count, in-process count, user correction count, or missing JD count.
- LLM evaluator: semantic judgment such as whether a skill is clearer, whether a JD matching strategy better reflects the user's goals, or whether a learning plan addresses the right gap.
- Human evaluator: explicit user acceptance, rejection, or correction.

Evaluator examples:

- Apply matching strategy: later application outcomes, fast rejection rate, in-process/interview rate, user acceptance of recommended target direction, and consistency with persona/intent.
- Site workflow skill: phase success rate, no-progress guard count, repeated action count, status-review completeness, unmatched review count, and user intervention frequency.
- Assistant router: user correction rate, confirmation rate for implicit saves, false-positive saves, and routing example quality.
- Workflow pattern memory: whether a new site skill draft becomes usable faster by reusing the pattern.
- Reporting summary: whether repeated user questions are answered directly by local summaries.

Cost and token usage can be evaluator inputs, but they should not be the primary evaluator when application quality or user fit is affected.

## Archive

The archive is the memory of evolution itself.

It should preserve:

- Candidate spec.
- Baseline evidence.
- LLM proposal.
- Applied draft or patch when any exists.
- Before/after snapshots for rollbackable targets.
- Evaluator output.
- Selection result.
- User feedback.
- Follow-up evidence.

The archive is what makes evolution cumulative. It prevents repeating failed hypotheses and lets successful changes become future context.

Suggested long-term run layout:

```text
workspace/evolution/runs/evo_run_xxx/
  run.json
  evidence_pack.md
  diagnosis.md
  proposal.md
  evaluation.json
  retention.json
  snapshots/
```

## Proposal / Mutation

The LLM should generate proposals from evidence and candidate specs.

Examples:

- Rewrite a site skill section to compact a stable workflow.
- Modify the project-level apply matching policy based on application outcomes.
- Add routing examples after repeated user corrections.
- Consolidate raw career conversations into durable memory units.
- Draft a new site skill from existing workflow patterns.
- Propose summary/report fields that answer repeated user questions.

Python should not try to encode all business reasoning. Python should build the context pack, enforce boundaries, and record the proposal.

## Selection

Selection decides what happens to a proposal.

Possible outcomes:

- `accepted`: evaluation evidence shows improvement.
- `rejected`: evaluation evidence shows regression or policy violation.
- `rollback`: an applied proposal made behavior worse and should be reverted.
- `keep_observing`: evidence is not enough yet.
- `superseded`: a newer proposal replaces this one.

Selection can become more autonomous over time, but it must depend on evaluator results and archive history.

## Safety Boundary

The evolution system may propose changes, but it must respect project safety boundaries.

Allowed to suggest:

- Skill edits.
- Router example updates.
- Memory consolidation.
- Report or summary improvements.
- Data repair or enrichment.
- Runtime configuration adjustments.

Requires human review:

- Any project-level skill change.
- Any site skill change that affects apply behavior.
- Any runtime configuration change that can increase cost, time, or parallelism.
- Any resume/persona/intent change that affects user representation.
- Any generated learning plan or application strategy that will influence future applications.

Do not auto-evolve:

- Provider API logic.
- Playwright/MCP protocol handling.
- Local browser action semantics or custom browser DSLs.
- Login security, password, MFA, CAPTCHA, or account-safety behavior.
- Automatic final-submit policy.
- Core storage schema.
- Arbitrary Python code.
- Git commit, push, or destructive workspace operations.

## Evaluation

Evolution is not successful because an LLM says it is better. It is successful only if later evidence supports it.

Evaluation signals include:

- Application outcomes: fewer fast rejections, more active/in-process/interview outcomes, fewer blocked applications.
- Matching quality: apply decisions better align with persona, CV, intent, and historical feedback.
- User feedback: fewer corrections, more accepted recommendations, clearer strategy discussions.
- Workflow reliability: fewer no-progress guards, fewer repeated page loops, fewer stale blocked jobs.
- Data quality: fewer unmatched reviews, more complete site job ids, cleaner JD/history records.
- New-site support: faster drafting of new site skills by reusing existing workflow patterns.
- Runtime cost: lower average phase time and token usage when it does not reduce result quality.

Cost and token usage are useful metrics, but they are not the primary goal. They should not override application quality or user fit.

## Retention

Retention decides what survives.

Outcomes:

- Accepted: evidence shows the change improved the intended metric or user experience.
- Rejected: evidence shows the change made behavior worse or violated policy.
- Keep observing: evidence is not enough yet.
- Superseded: a newer memory, skill rule, or candidate replaces the old one.

Retention should write back to local storage so the system remembers which hypotheses worked and which failed.

## LLM Role

The LLM should be used where flexible judgment is needed:

- Distilling raw events into durable memory.
- Interpreting application outcome patterns.
- Explaining user/job-market gaps.
- Drafting skill changes.
- Drafting new site skills from known workflow patterns.
- Summarizing interview preparation needs.
- Proposing evaluation plans.

The LLM should not be responsible for unsafe side effects without policy gates.

## System Role

The Python system should remain the host layer:

- Persist raw experience.
- Build structured evidence.
- Build compact context packs.
- Track candidates, runs, evaluations, and retention status.
- Enforce safety boundaries.
- Run deterministic checks and tests.
- Keep browser automation delegated to official browser/MCP tooling and Skills.

The system should not grow into a hard-coded recruiter model or browser action DSL.

## Markdown / Python / LLM Boundary

The framework should keep a clear boundary between Markdown, Python, and LLM responsibilities.

Markdown should define human-readable and LLM-readable knowledge:

- Skills.
- Candidate specs.
- Evolution policy.
- Workflow pattern descriptions.
- Diagnosis and proposal documents.
- Architecture and operator guidance.

Python should provide infrastructure:

- Read/write local storage.
- Count and aggregate evidence.
- Load candidate specs.
- Build evidence packs.
- Track evolution runs.
- Run deterministic evaluators.
- Persist archive records.
- Enforce safety boundaries.
- Execute tests and checks.

LLM should provide flexible intelligence:

- Distill raw experience.
- Interpret user/job-market gaps.
- Diagnose candidate weaknesses.
- Generate proposals and drafts.
- Compare semantic quality.
- Explain tradeoffs and risks.

This boundary keeps the project extensible without turning Python into a hard-coded recruiter or turning Markdown into an unreliable execution engine.

## Extensibility

Other users should be able to add new evolution behavior without editing core framework code for every case.

A future candidate spec should be enough to declare:

- Target object.
- Allowed change type.
- Evidence requirements.
- Evaluator requirements.
- Risk level.
- Output format.
- Rollback requirements.

Example future candidate spec:

```text
id: apply-matching-strategy
target_type: project_skill_section
target_ref: skills/search/jobs/SKILL.md#Apply Matching
allowed_change: skill_section_patch
evidence:
  - application_summary
  - history_jobs
  - apply_decisions
  - persona
  - intent
evaluators:
  - fast_rejection_rate
  - in_process_rate
  - user_strategy_acceptance
risk: high
review: human_required
```

This lets CareerEng become a framework for evolution rather than a project with a fixed set of hard-coded improvement directions.

## Implementation Roadmap

### V1: Evolution Memory And Review

Status: in progress.

Collect structured evidence and open improvement candidates from current local data:

- Browser phase events.
- Assistant bridge routing examples and corrections.
- Metrics.
- Application summary and history.
- Local memory signals.

Output review files and a compact context pack.

### V2: Evolution Framework Policy

Define framework policy and candidate boundaries:

- What counts as a candidate.
- What evaluator types exist.
- What the archive records.
- What can be proposed automatically.
- What can be applied automatically.
- What is outside evolution scope.

### V3: Candidate Specs And Trigger

Add candidate specs and simple triggers.

Trigger only answers:

```text
Is it worth starting an evolution run?
```

It should not decide how to change the system.

### V4: Evolution Run And LLM Diagnosis

Create explicit evolution runs with:

- Candidate.
- Scope and target.
- Evidence pack.
- LLM diagnosis.
- Candidate plan.
- Risk and validation plan.

No automatic patching in the first version.

### V5: Proposal, Review, Apply

Generate concrete drafts:

- Skill patches.
- Router example updates.
- Memory consolidation.
- Report improvements.
- Config recommendations.

Apply only after policy and human review.

### V6: Evaluation, Selection, And Retention

Compare before/after behavior:

- Application outcomes.
- Workflow stability.
- User correction rate.
- Data quality.
- Cost and time.

Accept, reject, rollback, or keep observing.

### V7: Career Intelligence

Use accumulated memory and evaluation data to improve:

- Apply matching strategy.
- User gap analysis.
- Learning/project plans.
- Interview preparation.
- New-site skill drafting.
- Employer/job-market understanding.

This is the long-term product value of evolution.
