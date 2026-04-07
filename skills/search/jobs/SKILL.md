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

## Live Page Rule

- After any action that can change the page state, use the new live page as the primary source of truth for the next decision.
- Use recent browser trajectory only as lightweight action history so you remember what was just attempted.
- Do not continue reasoning from the pre-action page once a fresh live snapshot is available.

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

### Goal

Prepare the site session so later job-flow phases continue inside the correct jobs system.
End `Session Preparation` as soon as the session is clearly ready for the next phase.

### Default Rule

Reuse any valid logged-in session if one already exists.
If the site is still in an authentication flow, continue that flow before doing downstream job work.
If the site requires password entry, verification, MFA, CAPTCHA, email confirmation, or another explicit human-only challenge, stop with `blocked`.

### Site Priority

Treat the active site skill as the primary source for site-specific login behavior, ready conditions, and stop conditions.
Do not treat public jobs UI by itself as proof of readiness unless the site skill makes clear that the current state belongs to the post-login jobs flow.
Treat clear signed-in identity signals such as candidate home, account menu, avatar, welcome banner, or profile entry inside the jobs flow as stronger evidence than the mere disappearance of a `Sign in` button.

### Don't

Do not drift into company research, job review, or filtering work before session readiness is clear.
Do not keep exploring once the current phase goal is already satisfied.

### Browser Context Ignore

- footer
- privacy
- legal
- terms of use
- support
- social links
- marketing content
- long informational text
- faq
- help center
- community
- promotional cards
- video section

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

### Browser Context Ignore

- footer
- privacy
- legal
- support
- social links
- marketing content
- about us
- company story
- benefits overview
- culture overview
- faq
- blog
- news
- unrelated help links

## Job Filtering

### Filtering Target

Apply the same default narrowing goal on every supported jobs surface unless the current site makes one item unavailable:

- `Location = China`
- `Role / Profession / Job Category = Software Engineering` or the closest visible software-engineering / engineering option
- `Employment Type = Full-time`
- Exclude `intern`, `internship`, `campus`, `graduate`, `new-grad`, `校招`
- Exclude `remote-only` roles when the page exposes a remote / work-site / work-location filter

### Filtering Rules

- Prefer visible official page filters over free-form search guesses.
- Use the smallest visible filter set that reaches the target. Prefer direct filters over layered filters that restate the same constraint.
- If a site exposes an engineering-family filter instead of an exact `Software Engineering` label, choose the closest official engineering option and continue.
- If a page exposes a work-site or remote filter, prefer `Onsite` or `Hybrid` and exclude `Remote` / `Remote only`.
- If a specific filter is not exposed on the current page, do not stall the phase trying to invent it elsewhere.
- If one applied filter already enforces a target dimension, do not reopen a second filter just to restate it. Example: if a direct remote toggle already excludes remote-only roles, do not also use `Work site` only to express the same exclusion.
- If the page already shows active chips, checked options, or URL state that satisfy one target dimension, treat that dimension as done and move on.
- Do not keep reopening filters after the main narrowing goal is already satisfied.

### Completion Rule

- Return `done` as soon as the current jobs surface is ready for retrieval after applying the clearly available high-value filters.
- Treat the phase as complete once the page has narrowed to China, full-time, software-engineering-oriented roles, and non-intern / non-new-grad roles to the extent the current site exposes those filters.
- After each successful filter application, re-check the live page and stop immediately if the current surface is already narrowed enough for retrieval.
- If remote-only exclusion is not available as a visible filter on the current page, do not keep searching for it forever; finish with the best available narrowing and hand off to retrieval.

### Don't

- Do not keep optimizing filters after the current jobs surface is already clearly narrowed enough for retrieval.
- Do not stack two different filters that mean the same thing once one of them already narrowed the page enough.
- Do not ask the user to confirm optional filter tweaks during this phase.
- Do not start retrieval inside this phase; finish filtering and return `done`.

## Job Retrieval

### Goal

Record the reachable jobs from the current narrowed jobs surface so later decision and apply phases can work from stored job entries.

### Recording Rules

- Record the full current visible jobs page before deciding whether to stop or paginate.
- Use `record_jobs` for the current page after extracting the visible job cards into structured data.
- Record only the current visible results cards from the live jobs surface. Do not scan the broader page for every `/job/` link outside the active results list or results region.
- Keep the extraction anchored to the real live results container. Do not treat a broad page section, full `main`, page header, side rail, or mixed detail panel as the jobs source when a narrower results list is visible.
- If one extraction step already produced the current page jobs, call `record_jobs` immediately before any more observation or pagination.
- If the live page clearly shows result signals such as a jobs count, pagination, or visible result cards but the current extraction returns zero jobs, do not conclude that the page is empty yet. Capture a fresh snapshot, re-identify the active results container, and then extract the current visible cards again.
- In that recovery step, keep the extraction anchored to the actual live results list or results region. Do not switch to broad page text, navigation sections, or unrelated content blocks.
- If the extraction output clearly looks like collapsed whole-page text instead of per-card records, do not treat it as a valid page extraction. Capture a fresh snapshot, re-identify the active results list, and extract the current visible cards again before paginating.
- In this phase, record lightweight list-level fields only: title, url, location, posted label, employment type, match label, apply state, and card text when visible.
- Do not open each job detail page just to capture long descriptions in this phase.
- Do not apply in this phase.

### Pagination

- After recording the current page, check the current site-specific stop condition.
- If no stop condition is triggered and a real next-page / next-results / load-more action is available, continue to the next results page and repeat.
- Use only a real visible pagination control, next-page control, or load-more action from the live page. Do not guess or synthesize pagination URLs.
- When paginating through numbered results, move sequentially page by page instead of jumping ahead from inferred totals or URL parameters.
- If the live page shows a total jobs count, page label, or other pagination signal that implies more results than the current recorded page, do not finish just because a next-page control is not immediately visible after one scroll. Re-check the bottom pagination region or results footer on the live page before deciding that pagination is unavailable.
- If the site exposes no further results page and no load-more action, finish the phase.

### Completion Rule

- Return `done` only after the current page has already been recorded and either the final page has been reached or the site-specific retrieval stop condition has been triggered.
- Do not treat the current page as final when the live page still indicates a larger total results set or additional numbered pages that have not yet been confirmed and recorded.
- Never stop before recording the full current visible page.

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
