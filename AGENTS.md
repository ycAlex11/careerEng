# AGENTS.md - CareerEng Assistant Rules

CareerEng is designed to be operated by Codex or another local AI assistant.

If the user uses `@career` or discusses job search, resumes, applications, interviews, target companies, career sites, reports, metrics, evolution, or CareerEng operations, read `docs/assistant_bridge/ASSISTANT_GUIDE.md`.

## Assistant Bridge

Use the assistant bridge before guessing commands:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "<user message>"
```

Use the returned `suggested_command` unless local evidence shows it is wrong.

Do not auto-execute high-impact CareerEng commands, especially job application runs, unless the user explicitly asks or confirms.

## Local Evidence

Before answering from memory, inspect local evidence when relevant:

- `workspace/reports/jobs/`
- `workspace/jobs/batches/`
- `workspace/sites/<site>/jobs/history_jobs.json`
- `workspace/sites/<site>/jobs/runs/`
- `workspace/metrics/`
- `workspace/action_cards/`
- `workspace/memory/`
- `workspace/evolution/`

## Common Assistant Workflows

- For application status or job-search requests, route through `assistant ingest` first.
- For report or summary questions, prefer reading local reports and application summaries before rerunning browser automation.
- For site Skill changes, make small targeted edits under `skills/search/jobs/sites/<site>/SKILL.md`.
- For project-wide job-search policy changes, edit `skills/search/jobs/SKILL.md`.
- For evolution work, inspect `docs/evolution/` and the relevant evidence pack before changing Skills or runtime behavior.
- For action cards, use `python -m careereng action-card list` and inspect the card before modifying files.

## Boundaries

CareerEng owns durable state, command execution, reports, metrics, history, and safety gates. Codex can inspect files, explain behavior, propose changes, and make targeted edits with user approval.

Keep Python focused on orchestration, persistence, safety, metrics, and rollback. Keep site behavior and business policy in Markdown Skills where practical.
