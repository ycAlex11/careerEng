---
id: application_strategy_evolution
name: Application Strategy And Gap Evolution
target_type: project_skill_strategy
target_ref: skills/search/jobs/SKILL.md#Apply Matching
risk_level: high
apply_policy: auto_draft_human_review_required
---

# Candidate: Application Strategy Evolution

## Purpose

Improve how CareerEng decides whether a visible JD should be applied to, and help the user understand the gap between their current profile and target roles.

This candidate is broader than apply matching. It uses application outcomes, rejection timing, JD signals, persona/CV evidence, and interview/status progression to produce:

- apply matching strategy proposals
- user gap analysis
- learning or project direction
- resume/persona/intent update suggestions
- long-term application strategy memory

## Required Evidence

Use these local sources:

- `workspace/application_summary/application_summary.json`
- `workspace/sites/<site>/jobs/history_jobs.json`
- `workspace/sites/<site>/jobs/runs/*.jsonl`
- `workspace/sites/<site>/applications/reviews/*.jsonl`
- `workspace/reports/jobs/`
- `workspace/profile/persona.md`
- `workspace/intent/intent.md`
- `workspace/cv/current/`
- `workspace/memory/application_feedback_signals.jsonl`
- `workspace/interviews/events.jsonl`

Useful evidence includes:

- submitted jobs and skipped jobs
- apply decision status, rule source, score, and reason when available
- JD text or JD summary
- site-native match signal
- days to rejection
- fast rejection clusters
- in-process, assessment, interview, or offer signals
- blocked or ambiguous applications
- repeated JD requirement clusters
- persona/CV coverage or missing evidence for those clusters

## Allowed Proposals

The LLM may propose:

- changes to the project-level `Apply -> Matching` skill section
- site-specific matching override suggestions if outcomes are site-specific
- strategy memory about role types that appear more or less promising
- gap analysis for missing skills, project evidence, domain experience, or resume wording
- learning or project plan candidates
- persona/intent update suggestions
- report/summary fields that make strategy outcomes easier to inspect

The LLM must not:

- directly edit the user's resume without explicit review
- infer unsupported experience
- treat rejection timing as definitive employer feedback
- auto-apply a project-level matching change without review
- overfit to a tiny number of outcomes

## Evaluators

Deterministic evaluators:

- fast rejection rate
- days to rejection by role cluster
- in-process / interview / assessment rate
- rejected-after-in-process count
- submitted / filtered / blocked distribution
- application review stage distribution
- unmatched review rate affecting trust in outcome data

LLM-assisted evaluators:

- JD requirement clustering
- whether rejected roles share requirement patterns absent from persona/CV
- whether successful or in-process roles share positive patterns
- whether a matching rule proposal is consistent with persona, intent, and CV
- whether a learning direction is realistic and tied to evidence

Human evaluators:

- user accepts or rejects the suggested strategy direction
- user confirms whether a gap is real
- user decides whether to update resume/persona/intent
- user decides whether to modify project-level matching rules

## Archive Requirements

Archive each evolution run with:

- baseline application summary
- evidence pack of relevant jobs and outcomes
- current `Apply -> Matching` section
- generated strategy hypothesis
- proposed skill patch, if any
- gap analysis
- learning/project direction
- evaluation plan
- user decision
- later outcome comparison when available

## Output Contract

A proposal should include:

- `strategy_hypotheses`
- `positive_role_signals`
- `negative_role_signals`
- `uncertain_or_low_evidence_findings`
- `skill_patch_proposal`
- `gap_analysis`
- `learning_direction_candidates`
- `resume_or_persona_update_suggestions`
- `evaluation_plan`
- `risk_notes`

## Selection Criteria

Prefer proposals that:

- distinguish weak signals from strong evidence
- use rejection timing carefully instead of treating it as explicit feedback
- improve JD matching without narrowing too aggressively
- help the user prepare for target roles, not only avoid rejection
- connect strategy to concrete persona/CV/JD evidence
- preserve human review for project-level matching policy changes
