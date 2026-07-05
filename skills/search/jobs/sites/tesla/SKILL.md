---
id: site-tesla
name: tesla Site Skill
version: v1
updated_at: '2026-07-05'
scope: site
site_key: tesla
status: ready
apply_enabled: false
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

## Apply

### Matching Override

- Describe any site-native matching or already-applied signals that should override the project default.

### Form Filling

- Describe site-specific form answers, safe defaults, and fields that require user takeover.

### Site Signals

- Describe what counts as already applied, submitted successfully, or clearly blocked on this site.

### Escalation

- Describe when the agent should stop and ask the user to take over.
