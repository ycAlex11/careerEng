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

## Phase Memory

- Use `update_phase_memory` to record durable in-phase state whenever a sub-step becomes clearly completed, clearly confirmed, still pending, or should not be repeated on the same unchanged page.
- Use `completed` for finished sub-steps, `confirmed` for stable live-page facts, `pending` for unresolved next targets, and `do_not_repeat` for loops that should not be retried unchanged.
- Clear stale phase-memory keys when the page state changes enough that an earlier pending or do-not-repeat item no longer applies.
- Do not rely only on recent trajectory when the current phase already has a clear completed/pending split; write that split into phase memory first.

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
- After a filter dimension is clearly satisfied, record that state in `update_phase_memory` before moving on to the next unresolved dimension.
- When the live page exposes a narrowed total such as `Show 64 jobs`, `64 jobs`, or a page label such as `1 of 7`, include structured `metrics` in `update_phase_memory` with `results_count`, `total_pages`, and `page_size` when those values are visible.
- If only the total jobs count is visible, record `metrics.results_count`; if only the pagination label is visible, record `metrics.total_pages`; if the current page's visible result count is clear, record `metrics.page_size`.

### Completion Rule

- Return `done` as soon as the current jobs surface is ready for retrieval after applying the clearly available high-value filters.
- Treat the phase as complete once the page has narrowed to China, full-time, software-engineering-oriented roles, and non-intern / non-new-grad roles to the extent the current site exposes those filters.
- After each successful filter application, re-check the live page and stop immediately if the current surface is already narrowed enough for retrieval.
- If remote-only exclusion is not available as a visible filter on the current page, do not keep searching for it forever; finish with the best available narrowing and hand off to retrieval.
- If one target dimension remains pending while another is already completed, record that pending/completed split in `update_phase_memory` instead of reopening the completed filter group again.

### Don't

- Do not keep optimizing filters after the current jobs surface is already clearly narrowed enough for retrieval.
- Do not stack two different filters that mean the same thing once one of them already narrowed the page enough.
- Do not ask the user to confirm optional filter tweaks during this phase.
- Do not start retrieval inside this phase; finish filtering and return `done`.

## Job Retrieval

### Goal

Record the reachable jobs from the current narrowed jobs surface so later decision and apply phases can work from stored job entries.

### Results Surface Guidance

- Treat the current live results page as the only source of truth for the current page.
- Read the current page from the visible results-list surface where the live page shows one current visible result entry per job.
- Prefer the current visible list or card region that corresponds to the current page's jobs. Treat that region as the current page's primary retrieval surface.
- Treat side detail panes, selected-job previews, recommendation rails, sticky preview panels, filter chrome, account chrome, job-alert panels, and pagination summaries as context only unless the active site skill explicitly says they are part of the current page's jobs source.
- A page label such as `4 of 7`, a total-count badge, or another pagination signal only means additional results pages still exist. It does not mean the current visible page is already recorded.
- The current page becomes recordable as soon as the current visible results surface yields concrete per-job `{title, url}` pairs for the current page.
- Once the current page is already recordable, call `record_jobs` immediately or after at most one final same-page pass for lightweight optional fields.
- Do not keep broadening or restarting observation on the same unchanged page once the current page is already recordable.

### Recording Rules

- Record the full current visible jobs page before deciding whether to stop or paginate.
- Start from the attached current live snapshot for the current results page.
- If the current visible jobs are already readable there, form the current-page records directly from that current page.
- If the snapshot is not yet enough, stay on the same current results page and use the official browser tools to read that same page. Do not leave the current results page before it is recorded.
- As soon as any same-page read already yields the current page's `{title, url}` set, treat that current page as ready to record.
- After `{title, url}` is already available for the current visible jobs, you may do at most one more same-page read to fill lightweight optional list fields that are clearly visible on the current page.
- Those optional fields are best-effort only. Do not keep observing the same page just to perfect location, posted label, employment type, match label, apply state, or posted_at.
- After that optional same-page pass, or immediately if it is not needed, call `record_jobs`.
- Do not paginate or finish while the current visible page is still unrecorded.
- The current results-page address is not a per-job link. Do not reuse the same results page address as the job link for multiple visible roles.
- Do not leave the current results page to open a separate job detail page before the current visible results page has been recorded.
- If one attempt already produced the current page jobs, call `record_jobs` before any more observation or pagination.
- In this phase, record lightweight list-level fields only: title, url, location, posted label, employment type, match label, and apply state when visible.
- Use a single stable phase-memory key named `retrieval_carry_forward` for page-to-page retrieval guidance in this run.
- The required per-page order is: `record_jobs` -> `update_phase_memory` -> pagination / next-page action.
- Immediately after `record_jobs` succeeds for the current page, and before any pagination action or `phase_result`, use `update_phase_memory` to save a short carry-forward retrieval context for the next page in this same run.
- Save that carry-forward retrieval context under `retrieval_carry_forward`.
- Write `retrieval_carry_forward` in exactly these four labeled lines and in this order: `Inspect First:`, `Worked Shape:`, `Ignore:`, and `Ready To Record:`.
- `Inspect First` must state the first same-page reading route to use on the next results page.
- `Worked Shape` must capture the decisive DOM or page-shape facts from the current successful page, such as whether the visible job cards are anchors or buttons, whether real per-job URLs appear directly in card `href` values, whether `aria-label` follows a stable `View job:` pattern, whether card ids follow a stable `job-card-...-job-list` shape, or whether visible card fields follow a stable order.
- `Ignore` must name the tempting but wrong current-page regions or URL sources that should not be reused on the next page.
- `Ready To Record` must state the minimum condition that means the current page is already ready for `record_jobs`.
- Keep that carry-forward retrieval context concise and reusable. Do not store raw selectors, stale element refs, raw browser-evaluate code, or page-specific container refs in phase memory. Short DOM or page-shape cues are allowed.
- Replace the existing `retrieval_carry_forward` note when a newer current-page reading route succeeds. Do not keep multiple competing retrieval carry-forward notes alive in the same run.
- It is acceptable for that carry-forward retrieval context to be imperfect. Prefer carrying the best current working note forward over re-starting every later page from a blank search.
- Do not open each job detail page just to capture long descriptions in this phase.
- Do not apply in this phase.

### Carry-Forward Usage

- If current phase memory already contains `retrieval_carry_forward`, the next results page's first same-page read must start from that carried guidance instead of restarting from a blank search.
- Treat `retrieval_carry_forward` as the default first route for the next page, not as optional background advice.
- On the next page, use `Inspect First` and `Worked Shape` before trying a different control type, region, or extraction route.
- If `Worked Shape` says the visible job cards are anchors, direct links, or another specific current-page shape, do not fall back to a different control-type route first unless the fresh current live page clearly disproves that shape.
- Use `Ignore` to suppress the wrong routes that already failed on the previous successful page.
- As soon as the current page satisfies `Ready To Record`, call `record_jobs` instead of continuing to explore.
- Only abandon the carried guidance after the fresh current live page clearly disproves it, such as returning empty on the visible results page or showing a different results shape than the carried note expected.
- If the carried guidance is disproved, switch to a new same-page reading route on that same live page and replace `retrieval_carry_forward` as soon as the current page becomes recordable.
- If the carried guidance still fits the current live page, keep using it through the current page's recording step instead of drifting into unrelated observation routes.
- Do not create a new retrieval carry-forward note before the current page is actually recorded.

### Recovery Rules

- If a same-page read returns zero jobs while the live page still shows jobs, result count, pagination, or another results signal, treat that read as a failed extraction route, not as an empty results page.
- When a same-page extraction route fails on a live results page, keep the current page unrecorded and try another same-page reading route from the current live page or the carried retrieval working note.
- During paginated retrieval, compare the current live page with `Current phase memory` totals, page count, page size, and recorded progress.
- If memory says more jobs remain but the current page shows empty results, `0 results`, `0 of 0`, or an unavailable state, do not call `record_jobs` with an empty page and do not mark that page complete.
- Refresh or reopen the current page, then read the current page again.
- If the refreshed page still conflicts with memory, stop retrieval and report the current page as abnormal instead of silently completing it.
- If the current page already yielded a non-empty jobs extraction, do not restart broad observation on that same unchanged page. Use the extracted current-page jobs to call `record_jobs` unless the extracted records are missing concrete per-job URLs.
- If current-page records are missing concrete per-job URLs, or if any visible role reuses the current results-page address as its job URL, resolve only those missing or invalid URLs from the same current visible page before calling `record_jobs`.
- Do not paginate, finish, or open unrelated detail pages while the current visible page has yielded jobs that have not yet been recorded.
- If the runtime reports that the current page fingerprint has already been recorded in this run, do not re-record or re-extract that same unchanged page. Move to the next real results page, load more results, or finish if the site stop condition is met.

### Pagination

- After recording the current page, check the current site-specific stop condition.
- If no stop condition is triggered and a real next-page / next-results / load-more action is available, continue to the next results page and repeat.
- Use only a real visible pagination control, next-page control, or load-more action from the live page. Do not guess or synthesize pagination URLs.
- When paginating through numbered results, move sequentially page by page instead of jumping ahead from inferred totals or URL parameters.
- After a page-changing action, start the new current page from the carried retrieval working note if one exists; do not reset to a blank exploration unless the fresh live page disproves that carried note.
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
- `recommended_apply` means the current job is approved to continue its apply flow. It is not a terminal outcome by itself.
- For every job, move on only after the current job reaches a true terminal run-state such as `filtered_out`, `already_applied`, `submitted`, or `blocked`.
- Submit only the jobs that are judged `recommended_apply`.
- Record JD sync, decision, and application progress when it helps preserve information without interrupting the visible live-page flow.
- Use `update_jobs` whenever the current job gains new JD data, decision state, apply state, submission result, or blocking reason.
- Treat `update_jobs` as a progress checkpoint, not as completion, unless that update writes a true terminal outcome for the current job.
- Treat apply as a page-by-page workflow on the current live page.
- At the start of each apply page, read the current live page first before taking the next action.
- Before making the final apply decision for a job, sync the current live JD, apply state, and any site-native match signal that the current page exposes for that job.
- If the live page already shows the next concrete action for a job approved to continue, such as `Apply Now`, `Next`, `Continue`, `Review`, `Save and continue`, or another visible forward action for the same job, take that action before writing an avoidable non-terminal progress-only update.
- Prefer a final `update_jobs` record when the job reaches a true terminal outcome; include the observed match label, decision, terminal application status, and any confirmed job id / job number in that terminal record.
- On the current page, first handle explicit validation errors, required empty fields, required selections, or missing uploaded files.
- If the current page needs a resume or file upload, first use that page's own upload entry such as `Upload new`, `Upload`, or `Select files`.
- Call `browser_file_upload` only when the current live page is upload-ready for that current page, either because it shows an active file chooser or because it already exposes a direct file-upload field.
- After any upload attempt, re-read the fresh current live page and confirm the page accepted the staged PDF before continuing.
- After any successful upload, `Next`, `Continue`, `Review`, `Save and continue`, `Submit`, or `Submit application` action, capture a fresh live snapshot and use that new page state as the source of truth.
- When a current-page apply sub-step becomes clearly complete, record that in `update_phase_memory` before moving on so later turns do not repeat it on the same unchanged page.
- Use the current page's safe forward action when one is clearly available, such as `Next`, `Continue`, `Review`, or `Save and continue`.
- If the current apply flow returns to a sign-in page but still shows a visible one-click continuation such as a provider button, remembered account, or direct sign-in continuation, continue through that visible login recovery path instead of stopping immediately.
- Treat ordinary email textboxes inside an application form as required form fields, not as human-only blockers.
- If the current apply flow has reached password entry, MFA, verification code, email confirmation code/link, an active CAPTCHA challenge, or another explicit human-only challenge with no visible one-click continuation left, finish that job with `blocked`.
- If the live page, active site skill, and lightweight apply facts are insufficient for a match decision or a required form answer, call `request_context` for the smallest needed bundle instead of guessing.
- Use the final irreversible action such as `Submit` or `Submit application` only when the current page is clearly the final confirmation page for the current job.
- Move to an irreversible submit action only when the current job is already `recommended_apply` under the active site or project matching rules.
- Do not keep retrying upload, forward, or submit blindly on the same unchanged page. If an action did not advance, re-read the current page and resolve the visible blocking reason first.

### Carry-Forward

- Use a single stable phase-memory key named `apply_carry_forward` for reusable apply guidance within the current site and batch.
- Immediately after the current job reaches a terminal apply state, and before `phase_result`, use `update_phase_memory` to save concise apply guidance under `apply_carry_forward`.
- Write `apply_carry_forward` in exactly these five labeled lines and in this order: `Inspect First:`, `Worked Flow:`, `Recurring Fields:`, `Ignore:`, and `Ready To Submit:`.
- `Inspect First` must name the first visible page region, section, or checkpoint to inspect on the next job on this same site.
- `Worked Flow` must summarize the reusable live-page flow that worked on this site, such as resume reuse, repeated profile sections, repeated review pages, or the safe forward actions that actually advanced.
- `Recurring Fields` must summarize the repeated required field families or repeated required choices that appeared across jobs on this site and how they were satisfied from the live page, the site skill, or lightweight apply facts.
- `Ignore` must name the tempting but wrong repeats to avoid on the next job, such as reopening an already-satisfied resume flow, re-uploading the same staged PDF, or revisiting a section that the current page already shows as satisfied.
- `Ready To Submit` must state the minimum live-page condition that means the next job is ready for final review or final submit.
- Keep `apply_carry_forward` concise and reusable. Do not store raw selectors, raw refs, raw DOM dumps, or raw browser tool code in phase memory.
- Replace the existing `apply_carry_forward` note when a newer completed job reveals a better reusable apply route for this same site and batch.
- If the current apply context already includes `apply_carry_forward`, start the next job's first live-page read from that carried guidance instead of restarting from a blank apply search.
- Treat `apply_carry_forward` as the default first route for the next job on this site, not as optional background advice.
- If the fresh current live page clearly disproves the carried guidance, adapt on that current page, finish the current job correctly, and replace `apply_carry_forward` when the current job reaches a terminal state.

### Recovery Rules

- After any successful `browser_file_upload` call, treat the page as changed and re-read the fresh current live page before taking any other apply action.
- If the fresh current live page confirms that the staged PDF is already uploaded or selected for this unchanged apply page, do not upload it again on that page. Continue from the next required form step, safe forward action, or final confirmation step that the live page shows.
- If the same unchanged apply page already completed a `browser_file_upload` call with the staged PDF in this run, do not repeat that upload on the same page. Return to the current live page state and continue the apply workflow from there.
- If the apply flow temporarily returns to a sign-in surface that still shows a visible sign-in continuation, do not treat that sign-in page as a normal job-state page. Continue through the visible login recovery path first, then resume the current job.
- If the apply flow is on a sign-in or recovery page, do not write final JD or application state updates from that page unless the live page clearly shows the job returned to a normal apply state.
- If an upload attempt leaves the page in an unresolved file-chooser or modal-only state and the normal form does not return, stop that job as `blocked`.

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
- For search-style comboboxes, typing a value does not by itself mean the field is selected.
- If the dropdown is still expanded or candidate options are still visible, the current field is not complete yet.
- Before moving to another field, finish selecting the current field's candidate option.
- When the page changes, re-read the current live page instead of relying on previous candidate options or old refs.
- Once one field establishes the right interaction path for this control style, reuse that same path for similar fields.
- If a required field cannot be answered from the live page, active site skill, or lightweight apply facts, call `request_context` for the smallest needed bundle, usually `full_cv` for detailed experience or `full_persona` for background constraints.
- If a required field already has a visible current value, selected option, checked state, or uploaded file, leave it as-is and move on.
- A passive CAPTCHA or anti-bot attribution label alone is not a blocker if normal required fields and forward buttons are still visible and usable.
- Do not spend time rewriting or re-answering fields that are already filled.
- Skip optional fields unless the active site skill explicitly requires them.

### Submission Success

- Treat a job as successfully submitted only after the site shows an explicit final success confirmation on the live page.
- If the site clearly shows that the user already applied, record `already_applied` instead of submitting again.
- If submission is ambiguous after the final action, do not guess success. Record the blocking state and stop for that job.

### Completion

- Finish `Apply` only after every saved job for the current site and batch has reached a terminal state.
- Do not leave jobs in an in-between state just because one role was submitted successfully.
