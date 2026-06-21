# CareerEng Codex Context

This file is the assistant-readable overlay for Codex and other local AI assistants.

It is intentionally smaller and more volatile than `ASSISTANT_GUIDE.md`. Evolution runs may update this file to summarize current routing guidance, memory-intake lessons, and assistant-facing behavior changes.

## Current Operating Boundary

- Treat explicit `@career` messages as intentional CareerEng requests.
- Ingest explicit `@career` messages before guessing commands.
- Do not silently save ordinary software-development conversation as CareerEng memory.
- For ambiguous career-related conversation, ask for confirmation before saving it as durable memory.
- Prefer updating `workspace/assistant_bridge/routing_examples.jsonl` with concrete examples over expanding hard-coded categories.
- When the user asks to summarize or persist recent Codex messages, first read `workspace/assistant_bridge/context/latest.md`, inspect existing memory and lessons, then preserve the requested window size with `python -m careereng assistant import-recent <file> --limit <N> --source-thread <thread_id> --source-client codex`.
- Do not claim CareerEng can read Codex's private thread history directly; Codex curates the visible thread context into candidate JSONL, and CareerEng validates/deduplicates/stores it.
- Split recent-message summaries before importing: career/profile/application/interview/evolution facts may become memory candidates; development task changes should become taskboard update suggestions; process chatter should be ignored.
- Do not write taskboard updates automatically. Ask the user first, then use `python -m careereng taskboard update <file>` after confirmation.

## Evolution Application Policy

- Low-risk assistant-router evolution may append routing examples.
- Low-risk assistant-router evolution may update this Codex context overlay.
- Changes to `ASSISTANT_GUIDE.md` should be proposed for review instead of automatically applied.
- Changes to `AGENTS.md` should remain manual and rare.

## What To Check First

- Prefer summary facts over Markdown presentation.
- Use reports as quick views, not as independent state.
- Use events/traces/snapshots only when debugging or explaining why a run failed.
- `workspace/assistant_bridge/intake_events.jsonl`
- `workspace/assistant_bridge/routing_examples.jsonl`
- `workspace/assistant_bridge/correction_events.jsonl`
- `workspace/assistant_bridge/intake_state.json`
- `workspace/taskboard/current.md`
- `workspace/memory/memory_units.jsonl`
- `workspace/application_summary/application_summary.json`
- `workspace/metrics/`
- `workspace/reports/`
- `workspace/evolution/runs/`
