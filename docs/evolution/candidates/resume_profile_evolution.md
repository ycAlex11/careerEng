---
id: resume_profile_evolution
name: Resume And Profile Evolution
target_type: resume_profile
target_ref: workspace/cv/current/*.md
risk_level: high
apply_policy: auto_draft_human_confirmation_required
---

# Candidate: Resume And Profile Evolution

## Purpose

Improve the user's resume, persona, and profile evidence from accumulated CareerEng experience.

This candidate turns long-term evidence into resume/profile suggestions:

- target company intelligence
- retrieved JDs and role clusters
- application outcomes
- positive-progress signals
- rejection patterns
- Codex/career conversations
- user-provided new projects, learning progress, and interview feedback

The goal is to help the user present true, evidence-backed experience more effectively for target roles.

## Source Of Truth

CV Markdown is the source of truth:

```text
workspace/cv/current/*.md
```

PDF is derived output:

```text
workspace/cv/exports/cv.pdf
```

After any accepted CV Markdown change, CareerEng should automatically regenerate the PDF. Site resume upload logic should continue to use the existing resume freshness mechanism:

```text
current CV Markdown updated_at > last successful site session_preparation
```

No site skill needs to maintain separate resume-sync state.

## Non-Goals

The LLM must not:

- rewrite the resume without explicit user confirmation
- add unsupported experience, skills, metrics, employers, projects, or seniority
- infer completed work from learning plans or interview preparation notes
- treat rejection timing as proof that a resume claim is bad
- automatically narrow the user's target companies or target roles
- change site skills, project skills, runtime code, provider code, or storage schema

## Required Evidence

Use these local sources:

- `workspace/cv/current/*.md`
- `workspace/cv/exports/cv.pdf`
- `workspace/profile/persona.md`
- `workspace/intent/intent.md`
- `workspace/evolution/memory/units.jsonl`
- `workspace/application_summary/application_summary.json`
- `workspace/sites/<site>/jobs/history_jobs.json`
- `workspace/sites/<site>/jobs/runs/*.jsonl`
- `workspace/sites/<site>/applications/reviews/*.jsonl`
- `workspace/assistant_bridge/intake_events.jsonl`
- `workspace/assistant_bridge/routing_examples.jsonl`
- `workspace/interviews/events.jsonl`

Useful evidence includes:

- user-confirmed new experience
- user-confirmed projects or learning progress
- repeated target-company skill demand
- repeated JD requirement clusters
- positive-progress role clusters
- fast rejection or rejection-pattern clusters
- interview feedback and interview questions
- current resume wording
- persona/CV facts that support or do not support a claim

## Allowed Proposals

The LLM may propose:

- resume gap analysis
- resume patch proposal for user review
- persona patch proposal for user review
- evidence that must be added before a resume claim is safe
- unsupported claims to avoid
- target-company tailoring notes
- project or learning suggestions that would create future resume evidence
- export requirement after accepted CV changes

The LLM must not propose:

- adding unsupported experience
- exaggerating skill depth beyond evidence
- editing CV/persona without user confirmation
- deleting important experience only because one company rejected a role
- automatically changing application strategy rules
- provider, MCP, browser protocol, security, or storage schema changes

## Trigger Contract

Trigger this candidate when:

- target company intelligence finds repeated skill or project gaps
- user asks to optimize the resume for a target company or role cluster
- user says they completed a project, learning plan, interview, or new work experience
- positive-progress roles suggest certain experience should be emphasized
- rejection patterns suggest the resume lacks evidence for repeated role requirements
- Codex/career conversations contain user-confirmed facts that are not yet reflected in CV/persona

## Output Contract

A proposal should include:

- `resume_gap_analysis`
- `resume_patch_proposal`
- `persona_patch_proposal`
- `unsupported_claims_to_avoid`
- `evidence_needed_before_resume_change`
- `target_company_tailoring_notes`
- `project_or_learning_evidence_plan`
- `export_required`
- `risk_notes`

## Apply Policy

Default behavior:

- Generate suggestions automatically.
- Require user confirmation before modifying CV Markdown or persona.
- After confirmed CV Markdown modification, automatically regenerate PDF.
- Record the resume/profile revision and change summary.
- Do not auto-upload the resume immediately; let the next site `session_preparation` use the existing `resume_upload_needed` context.

Allowed confirmed writes:

- `workspace/cv/current/*.md`
- `workspace/profile/persona.md`
- resume/profile memory units
- revision/change log

Disallowed automatic writes:

- CV Markdown without user confirmation
- persona without user confirmation
- site skills
- project jobs skill
- provider/runtime/browser code

## PDF Export Synchronization

When a confirmed proposal modifies `workspace/cv/current/*.md`, CareerEng should:

- archive the previous CV Markdown before writing the confirmed change
- regenerate `workspace/cv/exports/cv.pdf` from the updated Markdown
- mark the revision as upload-ready only if PDF export succeeds
- rely on `resume_upload_needed` in the next site `session_preparation` to decide whether each site needs a fresh upload

If PDF export fails, the CV Markdown change may remain as source truth, but the revision must be marked incomplete and must not be treated as ready for website upload.

## Evaluation And Selection Contract

Evaluate by target role/company context when available.

Positive indicators:

- user accepts the proposed resume/persona change
- changed resume better reflects user-confirmed facts
- later target-company applications show improved progress signals
- interview prep becomes more specific because resume evidence is clearer
- fewer blocked/unsupported answer cases occur for resume-backed questions

Negative indicators:

- user rejects or corrects the claim
- a proposed claim lacks evidence
- resume wording becomes too broad or misleading
- later applications reveal the same gap remains unresolved
- the change causes site resume upload to use stale PDF because export did not happen

Rollback behavior:

- If CV/persona files were changed, use snapshot rollback.
- If only memory was written, mark memory as `rejected`, `superseded`, or `low_confidence`.
- If PDF export failed, mark the resume revision as incomplete and do not treat it as upload-ready.

## Archive Requirements

Archive each evolution run with:

- current CV Markdown snapshot
- current persona snapshot
- target company intelligence references
- relevant JD/application/interview evidence
- generated resume/persona proposal
- user confirmation or rejection
- applied file snapshots when any file is changed
- exported PDF path and export status after confirmed CV change
- evaluation or rollback result

## Selection Criteria

Prefer proposals that:

- are grounded in user-confirmed facts
- improve alignment with target roles without fabricating experience
- preserve truthful scope and seniority
- make future website form answers easier to support from CV/persona
- connect resume wording to concrete JD/company evidence
- keep user control over all resume/persona edits
