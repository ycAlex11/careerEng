---
id: site-smoke-b
name: smoke-b Site Skill
version: v1
updated_at: '2026-04-03'
scope: site
site_key: smoke-b
status: draft
apply_enabled: false
---
# Site Skill

Use this file to describe how this site should be handled.

## Session Preparation

### Authentication

- Describe whether manual login is needed and where the login flow begins.
- Describe what account type should be used and any safe takeover points.

### Ready Signal

- Describe what the logged-in ready state looks like before discovery continues.

## Channel Discovery

### Navigation

- Describe how to reach the real jobs surface from the entry URL.
- Describe any known redirects, new tabs, ATS handoffs, or site-specific stop conditions.

### Success Signal

- Describe what should count as a real jobs list or reliable application entry.

## Apply Workflow

- Describe how to find relevant jobs and which pages are safe to skip.
- Describe when the agent should stop and ask the user to take over.
