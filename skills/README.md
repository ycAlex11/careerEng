# Skills Guide

This directory stores AI Skills (policy/procedure docs), not runtime code.

## What goes into skills

- Strategy and decision policy.
- Preference interpretation and conflict priority.
- Structured output expectations for LLM reasoning.

## What must stay in code

- Tool execution (Playwright calls, retries, timeouts).
- Data contract validation and schema checks.
- State transitions and safety gates (`y/n`, low-confidence guards).
- Persistent storage writes.

## Search skill layout

- `search/SKILL.md`: shared search chain for all domains.
- `search/jobs/SKILL.md`: project-level job-search policy and retrieval method.
- `workspace/skills/jobs/SKILL.md`: user-level job preference overlay created by `careereng onboard`.
- `search/people/SKILL.md`: people-search specific policy.

## Priority order

1. Current user message
2. Workspace user job skill
3. Project domain skill
4. `intent.md`
5. Code defaults/fallbacks
