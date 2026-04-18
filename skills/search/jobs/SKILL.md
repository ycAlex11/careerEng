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
- Use `record_jobs` for the current page as soon as the current visible jobs page can be formed into structured current-page records.
- Use the attached current live snapshot first. If the current visible jobs are already readable there, form the records directly from that current page instead of starting with extra extraction.
- If the fresh live snapshot clearly enumerates the current page's repeated visible job items, use that current-page visible count as the completeness check for this page.
- Do not paginate or finish while the current page records still contain fewer roles than that clearly visible current-page count.
- On split-view or mixed-panel job pages, first form the current visible results set for this page.
- If some fields or URLs are still missing, you may inspect additional candidate sources on that same page more broadly, but only keep per-role data that aligns back to the current visible results set.
- Do not accept or reject a same-page candidate source only because of its layout position, panel placement, or region label.
- Do not indiscriminately import every `/job/` link visible somewhere on the page. Keep only the links that can be matched back to the current visible results set for this page.
- The current results-page address is not a per-job link. Do not reuse the same results page address as the job link for multiple visible roles.
- Do not leave the current results page to open a separate job detail page before the current visible results page has been recorded.
- If one attempt already produced the current page jobs, call `record_jobs` immediately before any more observation or pagination.
- If one or more current-page list fields are still missing after reading the current live snapshot, use one focused supplemental read on the same current page, then call `record_jobs`.
- If any visible role on the current page still does not have its own concrete role link, stay on that same page, complete the missing links, and only then record or paginate.
- If the live page clearly still shows jobs, pagination, or a jobs count but the current attempt returned zero jobs, capture a fresh snapshot, use that live snapshot first, and try the same current visible jobs page once more before paginating.
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

## Apply

### Workflow

- Work from the current batch's saved jobs, one job at a time.
- Open each saved job URL, sync the current page's JD and application state, then decide what to do for that specific role.
- For every job, end in one terminal run-state before moving on: `recommended_apply`, `filtered_out`, `already_applied`, `submitted`, or `blocked`.
- Submit only the jobs that are judged `recommended_apply`.
- Record JD sync, decision, and application progress as you go so the current batch always reflects the latest state.
- Treat apply as a page-by-page workflow on the current live page.
- At the start of each apply page, read the current live page first before taking the next action.
- On the current page, first handle explicit validation errors, required empty fields, required selections, or missing uploaded files.
- If the current page needs a resume or file upload, first use that page's own upload entry such as `Upload new`, `Upload`, or `Select files`.
- Call `browser_file_upload` only after the live page has actually entered file-chooser state for that current page.
- After any upload attempt, re-read the fresh current live page and confirm the page accepted the staged PDF before continuing.
- After any successful upload, `Next`, `Continue`, `Review`, `Save and continue`, `Submit`, or `Submit application` action, capture a fresh live snapshot and use that new page state as the source of truth.
- Use the current page's safe forward action when one is clearly available, such as `Next`, `Continue`, `Review`, or `Save and continue`.
- If the current apply flow returns to a sign-in page but still shows a visible one-click continuation such as a provider button, remembered account, or direct sign-in continuation, continue through that visible login recovery path instead of stopping immediately.
- If the current apply flow has reached password entry, email entry, MFA, verification code, CAPTCHA, or another explicit human-only challenge with no visible one-click continuation left, finish that job with `blocked`.
- Use the final irreversible action such as `Submit` or `Submit application` only when the current page is clearly the final confirmation page for the current job.
- Do not keep retrying upload, forward, or submit blindly on the same unchanged page. If an action did not advance, re-read the current page and resolve the visible blocking reason first.

### Matching

- If the site skill defines a stronger site-native matching rule, use that site rule first.
- Otherwise use this common rule:
- Start from the current live JD, site-native signals, and lightweight apply facts already attached to the apply context.
- If those are not enough for the common rule, call `request_context` for `full_persona` before scoring.
- Score the JD against the available persona evidence.
- If the score is between 70 and 100, treat it as `recommended_apply`.
- If the score is below 40, treat it as `filtered_out`.
- If the score is between 40 and 70, call `request_context` for `full_cv` before making the final decision.
- After the full CV review, use `recommended_apply` only if the updated score is above 50; otherwise use `filtered_out`.
- Do not invent unsupported experience. If needed evidence is not attached yet, request the relevant context bundle instead of guessing.

### Resume Source

- The resume source for apply is the run-local staged PDF path provided in the current apply context.
- When the site asks for a resume upload, use that provided run-local PDF path.
- The apply context also provides the staged resume basename. If the current live page already shows that same file name as the selected or active resume for this apply page, treat the resume step as already satisfied and do not upload again.
- A successful file-upload tool call only means the local file was attached to the page control. It does not by itself prove the site accepted the resume.
- If the current live page already confirms that same staged PDF is uploaded or selected for this apply page, do not upload it again on that same page.
- If a stale file chooser reappears on an unchanged apply page after an upload attempt, return to the live page state first instead of blindly uploading again.
- Do not switch to an older site-saved application or a different local file unless the site skill explicitly requires a user takeover.

### Form Filling

- Fill only required fields in apply.
- Prefer site skill rules and lightweight apply facts for routine form filling.
- If a required field cannot be answered from the live page, active site skill, or lightweight apply facts, call `request_context` for the smallest needed bundle, usually `full_cv` for detailed experience or `full_persona` for background constraints.
- If a required field already has a visible current value, selected option, checked state, or uploaded file, leave it as-is and move on.
- Do not spend time rewriting or re-answering fields that are already filled.
- Skip optional fields unless the active site skill explicitly requires them.

### Submission Success

- Treat a job as successfully submitted only after the site shows an explicit final success confirmation on the live page.
- If the site clearly shows that the user already applied, record `already_applied` instead of submitting again.
- If submission is ambiguous after the final action, do not guess success. Record the blocking state and stop for that job.

### Completion

- Finish `Apply` only after every saved job for the current site and batch has reached a terminal state.
- Do not leave jobs in an in-between state just because one role was submitted successfully.
