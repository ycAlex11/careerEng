---
id: site-amazon-aws-china
name: amazon-aws-china Site Skill
version: v1
updated_at: '2026-03-11'
scope: site
site_key: amazon-aws-china
status: draft
apply_enabled: false
---
# Site Skill

Use this file to describe how this site should be handled.

## Session Preparation

- Describe the login intent for this site in semantic terms instead of hard-coding button text.
- Describe what a reusable logged-in session looks like.
- Describe when the agent should stop and hand control to the user.

## Channel Discovery

- Describe how to move from the entry URL into the real jobs surface.
- Describe any known redirects, region selectors, ATS handoff, or site-specific discovery stop conditions.
- Describe what counts as discovery complete, such as visible search controls, filters, jobs list, or role detail pages.

## Job Filtering

- Describe how the agent should narrow roles once the jobs surface is already visible.
- Prefer describing the page state and filtering goal, not fixed button labels.

## Apply Workflow

- Describe how to find relevant jobs and which pages are safe to skip.
- Describe when the agent should stop and ask the user to take over.
