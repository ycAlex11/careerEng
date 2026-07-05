---
id: application_strategy_evolution
name: Application Strategy And Gap Evolution
target_type: project_skill_strategy
target_ref: skills/search/jobs/SKILL.md#Matching Policy
risk_level: high
apply_policy: auto_draft_human_review_required
---

# Candidate: Application Strategy Evolution

## Purpose

Improve how CareerEng decides whether a visible JD should be applied to, and help the user understand the gap between their current profile and target roles.

This candidate is broader than apply matching. It uses application outcomes, rejection timing, JD signals, persona/CV evidence, and interview/status progression to produce:

- apply matching strategy proposals
- site or project `Matching Policy` calibration proposals
- user gap analysis
- learning or project direction
- resume/persona/intent update suggestions
- long-term application strategy memory

## Matching Policy Calibration

Use this candidate when evidence suggests that the project-level or site-level `Matching Policy` should change.

Matching-policy evidence includes:

- user corrections that a filtered-out role should have been considered or that an applied role should have been avoided
- repeated `decision_reason_type = matching_policy` filtered-out rows for a site or role cluster
- positive-progress applications that share a role cluster, JD signal, or site-native signal
- rejection or no-progress clusters that share unsupported requirements
- application feedback signals imported from Codex or `@career` conversation summaries
- company-specific demand patterns from `target_company_intelligence_evolution`

The LLM should decide whether the evidence supports:

- a project-level `Matching Policy` patch
- a site-level `Matching Policy` override
- a memory or accepted lesson without changing Skills yet
- a better evidence-gathering plan when the signal is weak

Python may count matching-policy-relevant evidence and create this candidate, but Python must not decide which role cluster is promising or what policy wording should change.

## Review-Gated Site Calibration

When a site accumulates enough matching-policy evidence, CareerEng may create a Codex-facing review card before creating a concrete proposal. Codex is the interaction layer for this review.

The review should:

- explain why the site reached the strategy-evolution threshold
- ask whether the user wants to evolve this site now
- list available evolution directions from existing candidate specs
- surface related accepted lessons, memory units, and action cards as optional evidence to inspect
- let the user include or reject cross-site lesson transfer before writing a proposal

Cross-site lessons are not automatically applied. Codex/LLM must inspect the evidence, decide whether it applies to the current site, and then write a concrete proposal using the existing proposal schema.

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
- `workspace/evolution/candidates/open.jsonl`
- `workspace/evolution/runs/`
- `workspace/evolution/browser_control/lessons.jsonl`
- `workspace/evolution/memory/units.jsonl`
- `workspace/action_cards/index.jsonl`

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
- user correction text that explicitly changes how a role should be matched
- previous matching-policy proposal, validation, or rollback records

## Allowed Proposals

The LLM may propose:

- changes to the project-level `Matching Policy` skill section
- changes to the project-level or site-level `Matching Policy` section
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
- `matching_policy_patch_target`
- `matching_policy_requeue_expectation`
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
