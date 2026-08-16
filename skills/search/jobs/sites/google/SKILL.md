---
id: site-google
name: google Site Skill
version: v1
updated_at: '2026-08-01'
scope: site
site_key: google
status: exploration
apply_enabled: true
job_identity: {}
---
# Site Skill

Use this file to describe how this site should be handled.

## Site Policy

- Retrieve and evaluate the complete candidate set allowed by the shared retrieval stop policy before deciding which Google roles to apply for. Do not submit early merely because the first matching role is visible.
- Before opening the first `Apply` entry in a Google batch, inspect the live Google Applications dashboard for an application-limit banner.
- If the dashboard states that applications cannot be submitted because the account has reached a limit such as 3 applications in a 30-day window, treat that as a site-wide apply blocker for the current batch.
- While the live limit is active, do not open additional job Apply entries, retry submission, edit the retained Careers profile, re-upload the accepted resume, or infer that preserved jobs are rejected or not-fit.
- Record the current apply item as blocked by `application_rate_limit`, pause the Google site, and preserve remaining jobs for a later eligible batch.
- Do not guess the cap reset date from local time. Resume only in a later batch after a fresh Applications-dashboard read confirms that the limit banner is gone.
- When the cap is absent, use the retained Careers profile and resume, complete only live required role fields, verify the review page, and use final `Apply` only for a job already judged `recommended_apply`.
## Matching Policy

### Application Gate

- Apply the shared matching policy to every retrieved candidate and rank eligible roles by final match score after required JD/CV review.
- Google permits at most three applications in a rolling 30-day window. When the live dashboard cap is absent, place only the top three ranked eligible roles in the apply list.
- If final scores tie, prioritize stronger direct technical evidence, then newer posting evidence when available.
- Roles outside the top three are deferred candidates, not rejected roles. Preserve them for a later eligible Google application window.

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

- Fill only visible required fields plus approved profile/resume facts. Leave optional education and experience fields empty unless the user explicitly asks to provide them.
- For education, provide university experience and any required country field; do not add other optional education or experience entries.

### Site Signals

- Treat a role as submitted only when the Google dashboard or completion page confirms Submitted.
- Treat the live three-applications-in-30-days banner as a site-wide application-cap condition, not as a job rejection or matching failure.

### Escalation

- Describe when the agent should stop and ask the user to take over.
