---
id: new_site_workflow_transfer
name: New Site Workflow Transfer
target_type: site_skill_bootstrap
target_ref: skills/search/jobs/sites/<new-site>/SKILL.md
risk_level: medium
apply_policy: auto_draft_human_review_for_apply_behavior
---

# Candidate: New Site Workflow Transfer

## Purpose

Use existing site operation experience to help draft a new company's site AI Skill.

This candidate exists for cases such as: the user asks to add or apply to a new target company, the site is registered, but the site-specific AI Skill is still a generic draft.

The first version should make the new site testable through non-apply phases:

- `session_preparation`
- `application_status_review`
- `channel_discovery`
- `job_filtering`
- `job_retrieval`

Automatic apply should stay disabled until the user explicitly approves site-specific apply behavior.

This candidate is a transfer mechanism, not a new browser executor. It should package existing CareerEng capabilities and local evidence so Codex can draft or refine a site AI Skill.

## Evolution Strategy

This is the bootstrap member of the `site_workflow_evolution` family.

Loop shape:

- Handoff loop: create or reuse the site record, target Skill template, action card, and evidence pack.
- Draft loop: Codex reads the router, this spec, project job Skill, mature site Skills, and site evidence to draft a testable site Skill.
- Test loop: later browser runs validate non-apply phases first. Failures feed `site_workflow_compaction` or `apply_form_workflow` depending on the phase.

Codex intervention points:

- when a new site has no mature site Skill
- when first-run evidence shows the template is too generic
- when a site is registered but apply behavior is not approved or not proven

Evidence-selection policy:

- Codex chooses mature reference Skills and evidence from the index. Python may list AMD, Microsoft, NVIDIA, Qualcomm, or other available examples, but should not decide which one is the right analogy.
- Prefer evidence from local site files, existing Skills, action cards, first-run traces, and failure snapshots.
- Keep new-site decisions in Markdown Skills and proposals. Do not add ATS-specific Python behavior.

## Reuse Contract

New-site workflow transfer must reuse existing project functions instead of creating a parallel implementation.

Reuse:

- site lookup and registration through `SiteStore.find_site()` and `SiteStore.register()`
- existing registration helper behavior through `SiteTools.handle_site_request()`
- entry URL discovery through `ChannelLocator.resolve_company_apply_channels()` when a URL is missing
- site AI Skill template creation through `SiteStore.ensure_skill_template()`
- action-card storage through `ActionCardStore.create_card()`
- existing registered-site workflow runtime for later testing
- existing site events and action-card events for audit history

Do not introduce:

- a separate new-site registry
- a separate site AI Skill template system
- a separate browser runner for new sites
- a Python branch for Apple, OpenAI, Workday, Greenhouse, Lever, or any ATS-specific behavior
- Python code that interprets page buttons, selectors, or browser intent

## Required Evidence

Use these local sources:

- target site AI Skill template
- project jobs Skill
- mature site AI Skills such as AMD, Microsoft, NVIDIA, and Qualcomm
- `workspace/sites/<site>/site.json`
- `workspace/sites/<site>/events/all.jsonl`
- relevant browser-control phase evidence when a first test run exists

Useful mature reference sites should be selected by available evidence, not hard-coded business similarity alone. AMD, Microsoft, NVIDIA, and Qualcomm are good first references because they already contain working site AI Skills and runtime history.

## Codex Interaction Contract

The primary integration surface is an action card.

The launcher should create or reuse a generic action card:

- `card_type`: `codex_draft`
- `metadata.task`: `site_skill_bootstrap`
- `metadata.site_key`: target site key
- `metadata.target_kind`: `ai_skill`
- `metadata.initial_test_scope`: the non-apply phases listed above
- `semantic_tags`: include `site_skill`, `new_site`, `workflow_transfer`, `codex_draft`, `browser_workflow`, and `non_apply_test`

Codex should read the card, inspect the related files, and draft or refine the target site AI Skill.

The action card should include:

- target site AI Skill path
- project jobs Skill path
- mature site AI Skill examples
- this candidate spec
- `workspace/sites/<site>/site.json`
- `workspace/sites/<site>/events/all.jsonl`
- first-run browser/evolution evidence when available
- `workspace/sites/<site>/evolution/workflow_memory.json` when available
- latest failed batch, trace, and failure snapshot when a test run has already failed

The launcher must not silently execute browser phases. It only prepares a task card and evidence path for Codex/user collaboration.

## Data Storage Contract

Use existing storage first.

Store:

- site identity in `workspace/sites/registry.jsonl`
- site details in `workspace/sites/<site>/site.json`
- site audit events in `workspace/sites/<site>/events/all.jsonl`
- target site AI Skill in `skills/search/jobs/sites/<site>/SKILL.md`
- action cards in `workspace/action_cards/open/`, `workspace/action_cards/index.jsonl`, and `workspace/action_cards/events.jsonl`
- browser/evolution evidence in existing `workspace/evolution/` paths
- later job retrieval data in existing `workspace/sites/<site>/jobs/` paths

Do not create a dedicated `workspace/site_bootstrap/` store in v1. Add a new store only if repeated bootstrap attempts need their own aggregation or analytics that cannot be reconstructed from site events, action cards, and evolution evidence.

## Allowed Proposals

The LLM may propose:

- drafting the target site AI Skill from the template
- adapting proven patterns from mature site AI Skills
- defining login readiness signals
- defining application-status review navigation and stop conditions
- defining job retrieval navigation, filtering, already-seen handling, and stop conditions
- keeping apply disabled and listing missing apply-specific evidence
- adding clear uncertainty notes that should be resolved by a first browser test
- extracting reusable workflow-transfer notes for future new sites

The LLM must not propose:

- adding Python browser-action semantics
- adding local selector DSLs
- hard-coding site navigation in provider code
- weakening login, MFA, CAPTCHA, or final-submit boundaries
- enabling apply without explicit user approval

## Safety Boundary

Default safety behavior:

- new site AI Skills should be `status: ready` so non-apply phases can be tested immediately
- new site AI Skills keep `apply_enabled: false`
- first tests should target non-apply phases
- first-run login for a newly added site is a human takeover boundary; do not let a new site continue as "login-ready" merely because the public jobs list is visible
- if the user explicitly requests applying to the new site, require a signed-in candidate/account state before downstream review, retrieval-for-apply, or apply phases continue
- human login, MFA, CAPTCHA, verification codes, and ambiguous required answers remain user takeover points
- final-submit behavior requires explicit user approval and site-specific apply instructions

The phrase "apply to a new company" should not bypass this boundary. It should first create a bootstrap/refinement card unless a testable site AI Skill already exists and apply has been explicitly enabled for that site.

## Runtime Boundary

Python may:

- create or find the site
- resolve an entry URL through existing locator logic
- ensure the site AI Skill template exists
- create or reuse an action card
- record site/action-card events
- print next-step commands

Python must not:

- execute browser phases as part of bootstrap
- decide which page element should be clicked
- encode ATS-specific navigation
- infer whether a new site can submit applications
- change phase order for a single site
- branch on arbitrary `metadata.task` or `semantic_tags`

Site-specific navigation and workflow behavior belongs in the site AI Skill.

## Completion Standard

V1 completion is deliberately simple: the bootstrap handoff is complete, or it failed to prepare.

`completed` means:

- the site is registered or already exists
- the site AI Skill template is created or already exists
- the Codex action card is created or reused
- the evidence pack and evolution run archive are created
- the action card links to the target site AI Skill, candidate spec, and evidence pack
- no browser phase is started by bootstrap
- `apply_enabled` remains false unless the user separately approves apply behavior

`failed_to_prepare` means one of the required handoff artifacts could not be created or linked.

Do not use token count, runtime duration, or no-progress-loop count as the primary success standard for this candidate. Those are optimization signals for later `site_workflow_compaction` or browser-control evolution. This candidate only answers whether a new site can be handed to Codex with enough local evidence to draft a testable site AI Skill.

## Evaluators

After Codex drafts or refines the site AI Skill, evaluation should focus on whether the generated site AI Skill can move through the non-apply phases without hidden Python browser behavior:

- session preparation reaches a clear ready signal or blocks correctly for user login
- application-status review records visible statuses or blocks with a useful reason
- job retrieval finds job rows or exits with a clear no-results/blocked reason
- apply remains disabled unless separately approved
- the generated site AI Skill contains enough concrete instruction for Codex/browser runtime to test without guessing
- failure states produce useful action cards or evidence instead of silent loops

## Phase Evidence Checklist

Do not define site-specific click paths here. Define what each phase should leave behind as evidence.

`session_preparation` should provide:

- logged-in ready signal, or a precise user-takeover reason
- login entry point used or attempted
- account/provider assumption when visible
- blocked reason for MFA, CAPTCHA, verification, password entry, or ambiguous user-only input
- for newly added sites, a public jobs list is not enough evidence for apply-ready session preparation; require user-completed login unless the run is explicitly retrieval-only

`application_status_review` should provide:

- whether an application dashboard, candidate home, profile dashboard, or application list was found
- visible raw application statuses when available
- whether active/inactive/submitted/rejected grouping exists
- status-review stop reason when no application list is available
- any unmatched review records that may need later enrichment

`channel_discovery` should provide:

- the real jobs surface or ATS handoff URL
- whether the entry URL redirected to a different career platform
- whether jobs require login or anonymous browsing is possible
- failure reason if no stable jobs surface is found

`job_filtering` should provide:

- whether project-level role/date/intern filters can be applied on the site
- which visible filters or search terms were used
- reason when filtering must be deferred to retrieval/card-level inspection
- any site limitation that prevents reliable filtering

`job_retrieval` should provide:

- job title and URL for each retrieved job
- location, posted label/date, and site job id when visible
- JD completeness or reason JD is missing
- whether a retrieved job was already known or needs enrichment
- reliable no-results, blocked, closed-position, or stop-policy reason when no jobs are retrieved

## Retry And Recovery Policy

Retry is phase-level and evidence-driven. It is not a low-level browser click retry policy.

Use:

- `max_strategy_attempts_per_phase = 5`

Attempt categories:

- `transient_retry`: page load delay, stale reference, temporary network instability, or short-lived disabled UI. Runtime may recover once or continue without changing site strategy.
- `strategy_retry`: the current path is likely wrong. The LLM may try another entry, tab, dashboard path, search phrase, filter path, or ATS route described by the site AI Skill.
- `blocked_no_retry`: login, MFA, CAPTCHA, verification code, password entry, or required user-only answer. Stop and request user takeover.
- `evidence_stop`: a clear outcome already exists, such as no jobs found, no applications found, position closed, apply disabled, posted-date stop condition, or reliable no-results page. Record the evidence and end the phase.

Rules:

- Blocked user-takeover states do not count as strategy failures.
- Evidence-stop outcomes should end the phase and should not consume all 5 attempts.
- Runtime no-progress guards may stop earlier than 5 attempts.
- Each strategy attempt should record what was tried, what changed, and why another attempt is needed.
- If the fifth strategy attempt still produces no useful evidence, stop the phase and create or recommend a `codex_debug` action card for site AI Skill revision.
- Do not continue browser exploration indefinitely just because the site is new.

## Selection Criteria

Selection applies to the new-site bootstrap evolution, not to final job application behavior.

`accepted` means:

- the target site AI Skill remains `apply_enabled: false`
- non-apply phases have at least one successful test, or they block at a legitimate user-takeover boundary
- retrieval either collects jobs with useful fields or records a reliable no-results/blocked reason
- status review either records visible application statuses or records a reliable dashboard-unavailable reason
- the site AI Skill is concrete enough for another run to test without rediscovering basic navigation
- no Python browser semantics, provider logic, or ATS-specific Python branch was introduced
- the action card or review trail records the result

`keep_observing` means:

- the site AI Skill is more concrete than the template but has not been tested yet
- only part of the non-apply flow has evidence
- login/user takeover prevented downstream evaluation
- entry URL or ATS handoff is plausible but not yet stable
- more browser evidence is needed before accepting or rejecting

`low_confidence` means:

- the draft is still too generic for reliable testing
- the draft copies mature site patterns without clear target-site evidence
- stop conditions are vague
- status-review or retrieval instructions lack visible signals
- evidence exists but is sparse or inconsistent

`rejected` means:

- the draft clearly does not match the target site
- repeated tests loop or fail without producing better evidence
- retrieval creates obvious dirty data, wrong URLs, or wrong job records
- the draft suggests unsafe apply behavior
- the proposal violates the browser automation boundary

`rollback_recommended` means:

- a site AI Skill patch was applied and made the non-apply flow worse
- `apply_enabled` was incorrectly enabled
- login/MFA/CAPTCHA/final-submit safety was weakened
- a previously working phase became blocked, failed, or looped after the patch

Bootstrap success does not mean apply is ready. Apply behavior must be drafted, approved, enabled, and evaluated separately.

## Archive Requirements

Archive:

- the action card
- the target site AI Skill before and after drafting
- referenced mature site AI Skills
- any first-run browser phase evidence
- test command and result summary
- site registration event
- action-card event rows
- candidate spec version used for the card

## Output Contract

The expected output is a drafted or refined site AI Skill plus an action-card trail, not a Python runtime change.

The draft should explicitly state:

- login entry and ready signals
- application-status review workflow
- retrieval workflow
- stop conditions
- known uncertainty
- apply remains disabled until reviewed

## First Implementation Shape

A future launcher command such as `python -m careereng site bootstrap "Apple" --url ...` should:

- reuse existing site registration and URL discovery
- ensure the site AI Skill template exists
- create or reuse the `codex_draft` action card
- return the site key, skill path, action card id, and action card path
- recommend that Codex reads the card before editing the site AI Skill

It should not run a browser test by itself.
