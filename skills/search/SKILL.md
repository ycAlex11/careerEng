---
id: search-core
name: Search Core Skill
version: v1
updated_at: "2026-03-08"
scope: search
---

# Search Core Skill

## Routing Rule

This file defines search-wide behavior shared across search domains.
If the current request belongs to job search, apply this core skill together with `./jobs/SKILL.md`.
If the current request belongs to people search, apply this core skill together with `./people/SKILL.md`.
If a workspace skill exists for the active search domain, load it as an overlay on top of the project search skills.

## Search Tools

Use Playwright as the default browser execution tool.
Use Google as the default search engine when web search is needed.
If a trustworthy entry URL is already known, open it directly before starting a new web search.

## Search Principles

Prefer first-party or official sources when the current task allows it.
Keep enough evidence to justify the result or next action.
Stop searching once the current stage objective has been satisfied.
Do not jump ahead into later-stage decisions when the current task is only discovery or navigation.
