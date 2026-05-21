# AGENTS.md

If the user uses `@career` or discusses job search, resumes, applications, interviews, target companies, career sites, or CareerEng operations, read `docs/assistant_bridge/ASSISTANT_GUIDE.md`.

Use the assistant bridge before guessing commands:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "<user message>"
```

Do not auto-execute high-impact CareerEng commands unless the user explicitly asks or confirms.
