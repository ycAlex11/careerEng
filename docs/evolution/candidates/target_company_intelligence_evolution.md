---
id: target_company_intelligence_evolution
name: Target Company Role Intelligence Evolution
target_type: target_company_intelligence
target_ref: workspace/evolution/memory/units.jsonl
risk_level: high
apply_policy: auto_memory_human_review_for_strategy_change
---

# Candidate: Target Company Role Intelligence Evolution

## Purpose

Build evidence-backed intelligence for each registered target company.

CareerEng treats registered active sites as target companies. The goal is not to judge whether a company is worth pursuing. If the user targets Microsoft, NVIDIA, Apple, AMD, Qualcomm, or another registered company, CareerEng should help the user understand that company's role requirements, feedback behavior, application outcomes, user gaps, and preparation path.

This candidate helps answer:

- what skills and experience this company's current roles repeatedly require
- which role clusters fit the user's current profile better
- which role clusters are currently lower priority because the gap is large
- what rejection, in-process, assessment, interview, or pending signals suggest
- what the user should learn, build, rewrite, or prepare next

## Non-Goals

The LLM must not:

- decide that a target company is not worth applying to
- tell the user to abandon a target company based only on rejection or pending status
- treat rejection timing as explicit employer feedback
- invent unsupported user experience
- automatically edit the user's resume, persona, or intent
- automatically weaken or narrow project-level application rules without user review

## Required Evidence

Use these local sources first:

- `workspace/sites/<site>/jobs/history_jobs.json`
- `workspace/sites/<site>/jobs/runs/*.jsonl`
- `workspace/sites/<site>/applications/reviews/*.jsonl`
- `workspace/application_summary/application_summary.json`
- `workspace/profile/persona.md`
- `workspace/intent/intent.md`
- `workspace/cv/current/*.md`
- `workspace/evolution/memory/units.jsonl`
- `workspace/reports/jobs/`

Useful evidence includes:

- retrieved JD records and JD summaries
- role titles, locations, seniority, employment type, and posted labels
- JD requirement language and skill keywords
- submitted, filtered, blocked, rejected, active, in-process, assessment, interview, and offer statuses
- rejection timing and fast rejection clusters
- long-pending applications
- status transition history
- user persona/CV/intent evidence
- prior company intelligence memory

V2 external evidence may include:

- interview reports from public sources
- Reddit, Blind, Glassdoor, LeetCode Discuss, company engineering blogs, or other public discussions
- public role/team articles that clarify real requirements

External evidence is not required for V1.

## Intelligence Areas

### Company Feedback Behavior

Analyze how this company appears to move applications through visible states:

- average or typical time to status change when enough data exists
- fast rejection count and timing buckets
- long-pending active/submitted/in-review applications
- in-process, assessment, interview, recruiter-screen, and offer signals
- status interpretation notes for this site

This analysis helps set expectations. It must not decide whether the company is worth pursuing.

### Company Skill Demand Profile

Analyze retrieved roles and JD text to identify:

- high-frequency core skills
- required skills versus bonus skills when visible
- role clusters such as AI infra, platform, backend, systems, embedded, hardware-heavy, test-heavy, applied ML, or data
- company-specific terminology
- recent demand signals from newly retrieved roles

### Negative Application Patterns

Analyze rejected or repeatedly filtered roles to find patterns that should be lower priority for the current user profile:

- hardware-heavy roles
- deeply embedded roles
- pure test roles
- seniority mismatch
- domain mismatch
- location or authorization mismatch
- required skill evidence missing from persona/CV

This should produce deprioritization guidance for role clusters, not company-level avoidance.

### User Gap Profile

Compare company role demand against persona/CV/intent:

- skills missing or weakly evidenced
- project evidence gaps
- domain experience gaps
- resume wording gaps
- facts that must not be claimed because they are unsupported

### Preparation Plan

Turn intelligence into actionable preparation:

- learning plan
- project plan
- resume/persona update suggestions for user review
- interview preparation targets
- role clusters to pursue now versus later

### External Role Intelligence

V2 should use public interview reports and role discussions not only for interview prep, but also for real requirement discovery:

- repeated interview topics
- practical skill expectations not obvious from JD
- company/team-specific technical focus
- preparation questions and mock interview targets

## Trigger Contract

Trigger by `site_key + intelligence_area`. Active registered sites are target companies.

### JD Demand Trigger

Trigger when:

- the same company has at least 30 valid local job records, or
- the same company has at least 15 new job records since the last intelligence evolution for this area

Output emphasis:

- `company_skill_demand_profile`
- `role_cluster_requirements`
- `user_gap_profile`
- `learning_project_plan`

### Rejection Pattern Trigger

Trigger when:

- the same company has at least 5 rejected history jobs, or
- the same company has at least 3 new rejected jobs since the last intelligence evolution for this area, or
- the same company has at least 3 fast rejections

V1 fast rejection means rejected within 7 days using `last_submitted_at`, `application_updated_at`, or `first_seen_at` as the best available basis.

Output emphasis:

- `negative_application_patterns`
- `avoid_or_deprioritize_role_clusters`
- `application_matching_suggestions`
- `user_gap_profile`

### Positive Progress Trigger

Trigger immediately when the same company shows any positive-progress status or stage:

- `in_process`
- `application_in_review`
- `assessment`
- `interview`
- `recruiter_screen`
- `offer`

Output emphasis:

- `positive_role_signals`
- `recommended_role_clusters`
- `interview_prep_targets`
- `user_gap_profile`
- `preparation_plan`

### Feedback Behavior Trigger

Trigger when:

- the same company has at least 10 application review records, or
- the same company has at least 5 long-pending applications

V1 long pending means an application is still active/submitted/application_received/in-review after at least 30 days using the best available local date.

Output emphasis:

- `company_feedback_behavior`
- `expected_response_timing`
- `status_interpretation_notes`
- `long_pending_summary`

## Allowed Proposals

The LLM may propose:

- company intelligence memory units
- role cluster requirement summaries
- user gap analysis
- learning or project plans
- interview prep targets
- application matching suggestions for human review
- resume/persona update suggestions for human review
- future external-intelligence search plans

The LLM must not propose:

- automatic company-level deprioritization
- automatic resume edits
- unsupported persona/CV claims
- automatic project jobs skill changes without user review
- browser automation Python changes
- provider, MCP, browser protocol, security, or storage schema changes

## Evaluators

Deterministic evaluators:

- number of valid local JD records
- number of new JD records since last evolution
- rejected count
- new rejected count since last evolution
- fast rejection count
- positive-progress count
- application review count
- long-pending count
- unmatched review count affecting trust

LLM-assisted evaluators:

- whether role clusters are evidence-backed
- whether gap analysis is consistent with persona/CV
- whether deprioritized role clusters are specific and not company-level avoidance
- whether the learning/project plan is actionable
- whether external evidence, when present, is cited and not overgeneralized

Human evaluators:

- user confirms a company/role direction
- user accepts or rejects gap analysis
- user accepts or rejects matching-rule changes
- user decides whether to update resume/persona/intent

## Evaluation Contract

Evaluate by `site_key + intelligence_area`.

Evaluation window:

- Observe the next 20 same-site applications after the proposal is applied, or
- observe the next 30 calendar days after apply,
- whichever comes first.

Same-site applications include submitted, rejected, active, in-process, assessment, interview, offer, blocked, apply-failed, already-applied, and filtered-out records. `filtered_out` should be counted separately because it reflects strategy influence rather than employer feedback.

Area-specific focus:

- `jd_demand`: whether role clusters, skill demand, user gaps, and preparation suggestions remain consistent with newly retrieved same-site JDs.
- `rejection_pattern`: whether fast rejection rate and repeated rejected clusters decrease without over-filtering plausible roles.
- `positive_progress`: whether similar role clusters continue to produce in-process, assessment, interview, recruiter-screen, or offer signals.
- `feedback_behavior`: whether pending, review, and status-change interpretation stays consistent with later same-site application reviews.

Quality gates:

- Do not lower the priority of a target company at company level.
- Do not turn a small number of outcomes into absolute rules.
- Do not create unsupported CV, persona, or experience claims.
- Do not automatically modify `skills/search/jobs/SKILL.md`.
- Do not treat rejection timing as explicit employer feedback.

Filtered-out records are part of the observation window because they show strategy behavior, but they must not be treated as employer feedback. They should be reported separately from submitted, active, in-process, rejected, or interview-related outcomes.

Positive selection indicators:

- same-site in-process, assessment, interview, recruiter-screen, or offer ratio increases
- same-site fast rejection ratio decreases
- repeated rejected role clusters decrease
- unsupported answer, blocked, or apply-failed outcomes decrease
- user accepts preparation, project, or resume suggestions
- new JD evidence confirms the generated company skill demand profile

Negative selection indicators:

- user corrections increase
- suggested role clusters lead to faster rejection
- plausible roles are over-filtered without evidence
- generated memory conflicts with later same-site evidence
- generated advice contains unsupported persona/CV claims
- company-level discouragement appears in the proposal

## Selection And Rollback Contract

Selection outcomes:

- `accepted`: follow-up evidence supports the intelligence or the user explicitly accepts it.
- `keep_observing`: the 20-application / 30-day window is incomplete or evidence is mixed.
- `low_confidence`: some evidence supports the intelligence but later data weakens it.
- `rejected`: evidence or user correction shows the intelligence is wrong.
- `superseded`: a newer company intelligence memory replaces this one.
- `rollback_recommended`: only when a file patch was applied and follow-up evidence indicates regression.

Rollback and retention behavior:

- For memory-only output, rollback means marking memory units as `rejected`, `superseded`, or `low_confidence`.
- For generated analysis that was wrong, record a correction and lower similar future inference weight.
- If a jobs skill patch is ever applied after human review, use the snapshot rollback path.
- Do not rollback by removing evidence records; evidence is append-only.
- Never rollback by deprioritizing or disabling a target company. The user decides which companies remain targets.

## Archive Requirements

Archive each evolution run with:

- target company/site key
- intelligence area
- trigger counts
- current application summary snapshot
- relevant history jobs and review rows
- persona/intent/CV evidence references
- prior intelligence memory
- generated intelligence proposal
- user decision if any
- later outcome comparison when available

## Output Contract

A proposal should include:

- `company_feedback_behavior`
- `company_skill_demand_profile`
- `role_cluster_requirements`
- `negative_application_patterns`
- `positive_role_signals`
- `external_role_intelligence_profile`
- `user_gap_profile`
- `learning_project_plan`
- `resume_or_persona_update_suggestions`
- `application_matching_suggestions`
- `interview_prep_targets`
- `risk_notes`

## Apply Policy

V1 may auto-write low-risk memory units when proposal validation supports it.

V1 must not automatically:

- edit `skills/search/jobs/SKILL.md`
- edit site skills for application matching
- edit resume, persona, or intent files
- change target company priority

If strategy changes are needed, generate a proposal for user review.

## Selection Criteria

Prefer proposals that:

- help the user pursue target companies more effectively
- separate company feedback behavior from company desirability
- use JD/review/history evidence instead of guesses
- identify specific role clusters, not vague categories
- produce actionable learning/project/interview preparation plans
- preserve human review for strategy-changing edits
