---
id: search-jobs
name: Search Jobs Skill
version: v1
updated_at: "2026-03-17"
scope: jobs
---

# Search Jobs Skill

## Input Priority

Use stage-aware priority when multiple sources overlap.

### General Order

Use this default order unless the current stage says otherwise:
1. current user request
2. workspace user job preference skill
3. project jobs skill
4. `intent.md`

### Site-Specific Override

When the current stage is tied to one registered site, treat that site's skill as the highest-priority site-specific rule source for that stage.
For site-specific stages, use this order:
1. current user request
2. site skill for the active site
3. workspace user job preference skill
4. project jobs skill
5. `intent.md`

Use `persona.md` and the current CV mainly when evaluating concrete jobs, not when only choosing target companies.
Return concrete employer names, not categories.

## Session Preparation

### Default Rule

For registered company job flows, prepare the site session before channel discovery, job retrieval, or apply decisions.
If a reusable logged-in session already exists for the site, reuse it.
If no valid session exists, open the site's persistent browser profile and let the agent try safe site-defined login actions first.
Only hand control to the user when the site requires password entry, verification, MFA, CAPTCHA, email confirmation, or another explicit identity challenge.

### Execution Goal

The goal of this stage is to make sure later steps run in the logged-in site state for every registered site.
Do not continue into downstream job-flow stages until the session is ready.

### Site Override

If the active site skill provides login entry hints, login success signals, or site-specific session notes, follow those site rules first.

## Company Discovery

### Default Scope

During company recommendation, focus first on company attributes instead of forcing an exact job title match.
Prioritize companies that match the user's company-level preferences, such as:
- Western foreign employers, especially US or Europe technology companies
- established engineering organizations
- full-time experienced-hire hiring patterns
- onsite or hybrid hiring in China when available

### Matching Bias

Treat AI-related products, infrastructure, or business lines as a bonus signal at this stage, not a hard requirement.
At the company stage, it is acceptable to keep companies that are a strong organizational match even if their current public role titles are not yet visible.

## Channel Discovery

### Default Start Point

After the user selects companies to register, begin from the known company entry URL if one is already available.
If no reliable company jobs entry is known, use Playwright plus Google search to locate it.

### Discovery Strategy

Prefer the official company website first, especially official careers or jobs pages.
If the official website is not enough, continue from the current site flow and look for likely hiring paths such as careers, jobs, open positions, workday, greenhouse, lever, or LinkedIn jobs.
Treat in-page navigation as part of channel discovery. If the company page requires clicking through items such as Careers, Open Positions, or a handoff into an ATS system, continue that navigation until a reliable job channel is reached.

### Stop Conditions

For each registered company, stop channel discovery as soon as one of these happens:
- a real jobs list, jobs table, job cards, or open-roles listing becomes visible
- a clear application entry page for that company's hiring system is found
- several reasonable search and navigation attempts fail

Do not treat a generic marketing page, informational page, or account page as a finished channel-discovery result.
If no reliable job-posting URL is found after a few reasonable attempts, stop and leave that company unresolved.

### Site Override

If the active site skill defines a preferred navigation path, preferred ATS handoff, or stronger success signal, use the site skill first.

## Job Filtering

### Default Role Focus

Once a company application URL or jobs page is found, inspect the published roles.
Prioritize software engineering, backend, platform, systems, and architecture-adjacent roles first.
Then treat AI-agent, agentic systems, and LLM-application roles as strong bonus matches.
When in doubt, prefer technically relevant engineering roles over broad unrelated roles.

### Filtering Rules

Before deciding whether a concrete role should move forward, apply these filtering rules first.

- Keep software-engineering roles and AI-architecture-adjacent roles first. Prefer titles and descriptions related to software engineer, backend engineer, platform engineer, systems engineer, AI application engineer, AI architect, or similar technical engineering work.
- If a role has a clear posted date, keep only roles posted on or after `2026-02-01`.
- If a role does not expose a clear exact posted date, prefer roles that appear to be within the last 30 days.
- Exclude internship, intern, campus recruiting, 校招, graduate, and new-grad roles.

## Apply Decision

### Default Matching Rule

Use these as the default application-decision rules when a site skill does not provide a stronger site-native signal.

- Score the job description against `persona.md` first.
- If the match score is between 70 and 100, apply directly.
- If the match score is below 40, do not apply.
- If the match score is between 40 and 70, review the full CV before making a final decision.
- After the full CV review, apply only if the updated score is above 50.

### Persona Usage

Use `persona.md` and the current CV here, when judging whether a concrete role is a realistic fit for the user.
