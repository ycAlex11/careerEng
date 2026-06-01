---
id: assistant_router_memory_intake
name: Assistant Router And Memory Intake Evolution
target_type: assistant_router
target_ref: careereng/integrations/assistant_bridge/
risk_level: medium
apply_policy: auto_draft_human_review_for_behavior_change
---

# Candidate: Assistant Router Memory Intake

## Purpose

Improve how CareerEng decides whether an external assistant conversation should enter local CareerEng memory.

The long-term direction is to move from explicit `@career` only toward reliable implicit detection, while preserving user control and avoiding noisy or incorrect memory writes.

## Required Evidence

Use these local sources:

- `workspace/assistant_bridge/intake_events.jsonl`
- `workspace/assistant_bridge/routing_examples.jsonl`
- `workspace/assistant_bridge/correction_events.jsonl`
- `workspace/assistant_bridge/action_events.jsonl`
- `workspace/assistant_bridge/thread_state.json`
- `workspace/memory/memory_units.jsonl`
- `workspace/memory/profile_signals.jsonl`
- `workspace/memory/intent_signals.jsonl`
- `workspace/memory/application_feedback_signals.jsonl`
- `workspace/interviews/events.jsonl`

Useful evidence includes:

- explicit `@career` messages
- active career thread follow-ups
- user corrections about wrong route or wrong command
- repeated career-related messages without `@career`
- positive routing examples
- negative or rejected routing examples when available
- Codex-curated batch memory imports
- promoted career memory units derived from assistant conversations
- assistant-suggested commands that the user accepted or rejected

## Allowed Proposals

The LLM may propose:

- new routing examples
- positive/negative examples for implicit career memory intake
- classifier rule changes
- thread-scope policy clarifications
- memory category refinements
- prompts that ask the user whether to save ambiguous career-related content
- consolidation rules for raw assistant signals

The LLM must not propose:

- automatically executing high-impact commands without user confirmation
- saving unrelated development conversations as CareerEng memory
- changing provider, MCP, browser protocol, or security behavior

## Evaluators

Deterministic evaluators:

- correction count by route/action
- routing example count by category
- explicit vs implicit trigger distribution
- number of saved events by memory category
- repeated correction patterns

LLM-assisted evaluators:

- whether a proposed routing example matches the user's intent
- whether an implicit-save rule is too broad or too narrow
- whether a thread-scope policy would create noisy memory

Human evaluators:

- user confirms that a conversation should be saved
- user rejects a suggested memory save
- user corrects the selected route or command

## Trigger Contract

Trigger this candidate when local assistant/memory evidence is large enough to review how CareerEng decides whether Codex conversations should become local career memory.

V1 trigger thresholds:

- cumulative explicit `@career` intake events >= 50
- new explicit `@career` intake events since last trigger >= 20
- cumulative unified career memory units >= 50
- new unified career memory units since last trigger >= 20
- new correction events since last trigger >= 5
- new Codex-imported memory units since last trigger >= 10

Business meaning:

- explicit `@career` intake events are positive examples of when the user intentionally wants CareerEng involved
- unified memory units show what assistant conversations actually became useful long-term career memory
- correction events are negative or corrective examples for route, category, action, or memory-quality mistakes
- Codex-imported memory units are curator examples produced from longer thread context

Trigger output should create an `assistant_router_memory_intake` evolution run with evidence from:

- raw assistant intake
- routing examples
- corrections
- action suggestions
- unified career memory units
- Codex-curated memory imports

V1 must not automatically enable implicit saving for messages without `@career`. The safe first-stage behavior is to improve examples, curation guidance, suppress rules, and confirmation policy so Codex can ask whether a conversation should be saved.

## Review Pack Contract

V1 evaluation is review-pack based, not a fully automatic evaluator.

CareerEng should generate a Codex-readable review pack that includes:

- trigger reason
- post-apply assistant intake events
- post-apply career memory units
- post-apply corrections
- Codex-imported memory units
- category distribution
- duplicate-memory estimate
- explicit-intake-to-memory proxy
- correction-to-memory ratio
- representative memory, correction, and intake samples

The review pack should be saved in the evolution run archive under:

```text
workspace/evolution/runs/<run_id>/evaluations/codex_review_pack.md
```

V1 default evaluation status is:

```text
needs_codex_review
```

This means the project prepared evidence for Codex and the user, but did not automatically decide whether the classifier/curator improved.

## Codex Review Questions

Codex should help the user answer:

- Do sampled memory units have clear source evidence?
- Are categories assigned correctly?
- Are summary, facts, entities, and tags useful for later resume/profile, application strategy, target-company intelligence, or interview preparation work?
- Did CareerEng save ordinary development chatter, temporary commands, or process-control messages that should have been suppressed?
- Did the evidence suggest obvious high-value career content that was not promoted into memory?
- Do correction events indicate a route, category, save policy, or curation rule that should be adjusted?
- Should the next status be `accepted`, `keep_observing`, `low_confidence`, `rejected`, or `rollback_recommended`?

## Non-Goals For V1 Evaluation

V1 evaluation must not:

- automatically accept or reject a classifier/curator change
- automatically rollback a run only from memory statistics
- compute fake model-performance metrics
- enable implicit saving without user confirmation
- modify Python routing rules
- modify memory units while reviewing them

## Archive Requirements

Archive each evolution run with:

- evidence pack
- routing examples before/after
- proposed classifier or prompt changes
- accepted/rejected examples
- user confirmations and corrections
- evaluation result

## Output Contract

A proposal should include:

- `routing_examples_to_add`
- `routing_examples_to_suppress`
- `classifier_rule_suggestions`
- `thread_scope_policy_suggestions`
- `memory_category_suggestions`
- `evaluation_plan`
- `risk_notes`

## Selection Criteria

Prefer proposals that:

- reduce repeated user corrections
- improve implicit save suggestions without increasing false positives
- preserve explicit user control for high-impact actions
- create reusable routing examples
- keep memory categories stable and understandable
