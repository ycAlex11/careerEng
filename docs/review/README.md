# Review Packs

Review packs are Codex-readable evidence bundles for human-assisted review.

They are intentionally separate from assistant integrations and evolution logic:

- `assistant_bridge` receives and classifies external assistant messages.
- `career_memory` stores long-term career memory.
- `evolution` triggers and archives candidate runs.
- `review` packages evidence so Codex and the user can inspect whether stored data or a proposed change is useful.

## Boundary

The review layer may:

- collect local evidence
- compute simple local metrics
- sample rows for inspection
- render Markdown review packs
- save review metadata

The review layer must not:

- decide business truth by itself
- mutate skills, memory, resume, profile, or runtime code
- run browser automation
- replace user selection
- automatically accept or rollback evolution runs

## First Review Type

The first review type is `assistant_router_memory_intake`.

It reviews whether Codex/CareerEng is storing useful career-memory evidence from assistant conversations.

The output is:

```text
workspace/evolution/runs/<run_id>/evaluations/codex_review_pack.md
workspace/evolution/runs/<run_id>/evaluations/review_pack.json
```

Codex should read the pack and help the user decide whether the next status should be:

- `accepted`
- `keep_observing`
- `low_confidence`
- `rejected`
- `rollback_recommended`
