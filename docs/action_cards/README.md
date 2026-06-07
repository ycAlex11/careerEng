# Action Cards

Action cards are local Markdown task cards for work that should be handed to Codex or the user instead of being hidden inside an automated run.

They are intentionally small:

- one goal
- one reason
- related evidence files
- suggested Codex actions
- safe close/cancel commands

## Storage

Action cards live under:

```text
workspace/action_cards/
```

Primary files:

- `workspace/action_cards/open/<card_id>.md`
- `workspace/action_cards/done/<card_id>.md`
- `workspace/action_cards/cancelled/<card_id>.md`
- `workspace/action_cards/index.jsonl`
- `workspace/action_cards/events.jsonl`

`index.jsonl` is the searchable card index. `events.jsonl` records card lifecycle events.

## Commands

```bash
python -m careereng action-card list
python -m careereng action-card show <card_id>
python -m careereng action-card close <card_id> --result "reviewed and accepted"
python -m careereng action-card cancel <card_id> --reason "not needed"
```

## Current Integration

The first integration is assistant-memory evolution review.

When `python -m careereng evolution evaluate --run <run_id>` evaluates an `assistant_router_memory_intake` run, CareerEng writes a Codex review pack and creates an action card pointing to that pack.

The card tells Codex what to inspect and how to close the review.

Site registration is the second integration.

When a newly registered site has only a new site AI Skill, CareerEng creates a `codex_draft` action card with `metadata.task = "site_skill_bootstrap"`. Codex can then inspect mature site AI Skills and refine the new site's site-specific workflow without adding Python browser-action logic. The Skill should be testable with `apply_enabled=false`; apply behavior remains disabled until explicitly approved.

## Type Boundary

`card_type` describes the collaboration mode, not the business task.

Keep `card_type` stable and small:

- `codex_review`: ask Codex to review evidence and recommend a status.
- `codex_draft`: ask Codex to draft or refine a file, plan, or local artifact.
- `codex_debug`: ask Codex to diagnose a failure from logs and evidence.
- `human_action`: ask the user to complete a manual step such as login or MFA.
- `manual_decision`: ask the user to choose or approve a direction.

Business-specific details must live in `metadata`, especially `metadata.task`.

Open-ended semantic grouping should live in `semantic_tags`.

Use these fields this way:

- `card_type`: stable collaboration mode.
- `metadata.task`: human-readable business task name.
- `semantic_tags`: open semantic labels for future retrieval, routing, clustering, and evolution.

Examples:

- New site AI Skill refinement: `card_type = "codex_draft"`, `metadata.task = "site_skill_bootstrap"`.
- Application summary review: `card_type = "codex_review"`, `metadata.task = "application_summary_review"`.
- Failed site workflow diagnosis: `card_type = "codex_debug"`, `metadata.task = "site_skill_refinement"`.

Do not add a new Python action-card type for every business scenario. Add a new `metadata.task` value, richer card content, and semantic tags first. Python should only grow when a reusable storage, lifecycle, or evidence-linking capability is missing.

Python must not branch on arbitrary `semantic_tags`. Tags are for retrieval and later LLM-assisted interpretation, not for hidden business handlers.

## Boundary

Action cards do not execute browser actions and do not replace Skills.

They are a collaboration layer for local review and follow-up work:

- Codex reads the card and evidence.
- For site Skill refinement, Codex should inspect workflow memory, latest failed batch, trace, and failure snapshot before editing a site Skill when those files are linked.
- The user or Codex records the result.
- CareerEng stores the lifecycle locally.

In v1, action cards do not automatically promote content into career memory or apply evolution changes.
