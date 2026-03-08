---
id: search-core
name: Search Core Skill
version: v1
updated_at: "2026-03-08"
scope: search
---

# Search Core Skill

## Routing Rule

If the current request is about finding companies for job search, always apply this core skill together with `./jobs/SKILL.md`.
If the user has already selected companies and the system is locating where to apply, also continue using `./jobs/SKILL.md`.
If a workspace user job preference skill is available, load it as an additional overlay for personal preferences.

## Search Tools

Use Playwright for browser actions.
Use Google as the search engine when web search is needed.

## Search Context

For company-finding and company-follow-up search, reason over the current user message first.
Then apply the workspace user job preference skill if present.
Then apply the project search job skill.
Use `intent.md` as a structured fallback state, not as the highest-priority preference source.
Use `persona.md` mainly when evaluating whether concrete jobs are a good fit for the user.
