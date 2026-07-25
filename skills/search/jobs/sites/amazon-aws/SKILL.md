---
id: site-amazon-aws
name: amazon-aws Site Skill
version: v1
updated_at: '2026-07-24'
scope: site
site_key: amazon-aws
status: exploration
apply_enabled: true
job_identity: {}
---
# Site Skill

Use this file to describe how this site should be handled.

## Site Policy

### Retrieval Policy

- Describe site-specific posted-window rules, pagination stop rules, and whether old roles should still be recorded.

### Job Identity Policy

- If this site uses hash-routed job detail URLs, declare the pattern in front matter, for example `job_identity.fragment_job_route_patterns: ['#/job/{site_job_id}']`.
- Record real job detail URLs and site-native job IDs when visible; do not record a generic company jobs-list URL as a job URL.

### Application Review Policy

- Describe how this site exposes submitted, active, inactive, rejected, closed, or withdrawn applications.

## Matching Policy

### Application Gate

- Describe site-native match labels, hard exclusions, and when the shared project matching rule should be used.

## Session Preparation

### Authentication

- Reuse the authenticated Amazon Jobs session when it is still valid.
- When Amazon Jobs or Passport requires authentication, use the visible Google sign-in or Google account continuation route. The browser profile owns the saved Google account; do not ask for, store, or type account credentials in CareerEng files.
- If Google account selection, password entry, MFA, CAPTCHA, or another explicit account-security step requires user input, keep the current page open and return `blocked` with the exact human-only action needed.

### Ready Signal

- Treat a reachable protected Amazon application dashboard, or a signed-in Amazon Jobs menu that can open that dashboard without another sign-in prompt, as ready.

## Channel Discovery

### Navigation

- Describe how to reach the real jobs surface from the entry URL.
- Describe any known redirects, new tabs, ATS handoffs, or site-specific stop conditions.

### Success Signal

- Describe what should count as a real jobs list or reliable application entry.

## Apply

### Matching Override

- Describe any site-native matching or already-applied signals that should override the project default.

### Form Filling

- Describe site-specific form answers, safe defaults, and fields that require user takeover.

### Site Signals

- Describe what counts as already applied, submitted successfully, or clearly blocked on this site.

### Escalation

- Describe when the agent should stop and ask the user to take over.
