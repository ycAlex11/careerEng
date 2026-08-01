---
id: site-amd
name: AMD Site Skill
version: v1
updated_at: '2026-06-16'
scope: site
site_key: amd
status: ready
apply_enabled: true
---
# AMD Site Skill

## Site Policy

### Retrieval Policy

- Retrieve AMD jobs from the current filtered live jobs surface before any stop decision.
- Treat posted-date / posted-age rules as apply-candidate eligibility first, not as an immediate pagination stop.
- Stop retrieval only after the current page has been recorded and a safe site/project stop condition is met.
- Preserve successful retrieval carry-forward guidance for the current AMD result-card link shape.

### Application Review Policy

- Review AMD submitted applications only after login is confirmed.
- AMD may land directly on a dashboard or past-submittals surface after login; use the visible application table as the review source.
- Record visible raw statuses when AMD shows them, and use the table/section label when row-level status text is not visible.

## Matching Policy

### Application Gate

- AMD uses the shared project matching rule unless a live page exposes a clearer site-native application state.
- Re-check the live JD and application state before opening the apply flow.
- Hard-exclude intern, campus, student, new-grad, co-op, 校招, and 实习 roles before opening any apply flow.

### AMD Matching Override

- For AMD, treat a live AI or software-engineering role with clear technical-stack overlap as a positive apply candidate. Relevant overlap includes Python, C++, AI/ML systems, LLM/agent applications, RAG, computer vision, simulation platforms, GPU software, AI frameworks, performance tooling, testing, CI, and developer infrastructure.
- Do not require an exact prior AMD/ROCm/GPU-specialist title when the live JD's core work remains compatible with the candidate's demonstrated software and applied-AI background. Record the concrete overlap in the matching rationale.
- Hard-exclude a role only when the live JD explicitly requires `10+ years` of relevant experience, or when its core work is low-level graphics/platform driver development, firmware/ASIC bring-up, electrical/hardware architecture, or another specialist domain for which the candidate has no supporting evidence.
- Do not exclude a role merely because it is senior. If the JD does not explicitly require `10+ years`, evaluate the actual technical stack and responsibilities first.

## Session Preparation

### Goal

- Complete AMD candidate login and leave the browser ready for downstream phases.

### Authentication

- Use the AMD careers candidate flow, not a generic AMD marketing page.
- If the current AMD jobs page shows `Returning User Login`, `Return to Login`, `Sign In`, or similar, click it during `Session Preparation` to reach the AMD login surface.
- On the AMD login surface, use the visible `LinkedIn` account login option.
- Continue through visible LinkedIn or remembered-account continuation steps when they are one-click browser actions.
- If a LinkedIn or AMD login page shows a saved/filled credential state with a visible `Sign in`, `Continue`, `Log in`, `登录`, or equivalent forward button, click that forward button once before declaring the flow blocked.
- Do not read, expose, copy, or modify saved passwords. Only use visible one-click continuation actions when credentials are already filled or remembered by the browser.
- If the flow reaches password entry, MFA, verification code, CAPTCHA, email confirmation, or another explicit human-only challenge with no visible one-click continuation left, stop with `blocked`.

### Ready Signal

- AMD signed-in dashboard signals override any visible public/header login links.
- Treat `Session Preparation` as complete only when AMD is signed in.
- Strong AMD signed-in signals include AMD candidate dashboard, visible signed-in identity, `Log Out`, `Past Job Submittals`, submitted-application table, or a comparable post-login candidate account surface.
- If the signed-in AMD dashboard, submitted-application table, or `careers-amd.icims.com/jobs/dashboard` candidate surface is visible, record login readiness and finish `Session Preparation` immediately.
- Once any AMD signed-in dashboard signal is visible, do not click `Returning User Login`, `Return to Login`, `Sign In`, or any other login entry again during `Session Preparation`.
- If an AMD dashboard URL is open but the page is still loading or visually sparse, wait and re-read the page once before deciding; do not open another login tab from that dashboard surface.
- Do not use AMD dashboard profile, candidate profile, account-maintenance, timezone, or generic profile-update routes during `Session Preparation` just to update the resume. AMD's dashboard profile route is not a reliable standalone resume manager.
- Defer AMD resume upload to the concrete job apply flow. When an apply flow reaches `Candidate Profile`, follow `AMD Candidate Profile / Resume Update Handling`.
- Do not treat AMD public jobs search, generic login page, profile chooser, marketing page, or the mere presence of searchable jobs as login-ready.
- If `Returning User Login` or another sign-in entry remains visible on a public AMD jobs/search page with no signed-in dashboard signal, login is not complete.

### Login Entry Boundaries

- `Session Preparation` is the normal place to establish AMD login.
- During AMD search phases (`Channel Discovery`, `Job Filtering`, and `Job Retrieval`), do not click `Returning User Login`, `Return to Login`, `Sign In`, or other account login entries.
- During search phases, if the current page is already a real AMD jobs-search surface, continue the search work even if a header login link is visible.
- During search phases, if login is unexpectedly required before jobs can be searched, stop the phase with `blocked` instead of starting a new login flow.
- During `Session Preparation`, do not click AMD dashboard noise links such as timezone labels or `Singapore Standard Time`.
- Do not open AMD profile/resume/account-maintenance routes during `Session Preparation`; those routes are handled only inside a concrete job apply flow when AMD presents `Candidate Profile`.

## Channel Discovery

### Navigation

- From the AMD careers entry surface, click `View Current Job Opportunities` or the closest visible AMD jobs-search entry.
- If the current page already shows AMD job search controls, real job result cards, or a searchable AMD jobs list, stop discovery immediately.
- Stay inside the AMD careers/jobs flow.

### Success Signal

- Treat discovery as complete only after a real AMD searchable jobs UI or visible jobs results list is available.

## Job Filtering

### Filtering Goal

- Apply the AMD targets when the page exposes them: `Location = China`, `Categories = Engineering`, and `Recent Graduation = No`.

### Filtering Direction

- First complete `Location = China`, then complete `Categories = Engineering`, then complete `Recent Graduation = No`.
- After selecting the current AMD filter option, immediately press `Escape` to close the current dropdown.
- If the dropdown is still open, continue pressing `Escape` until it is closed; do not do anything else first.
- If the dropdown is still open, do not look for the next filter, do not scroll, and do not end `Job Filtering`.
- Only continue to the next AMD filter after the dropdown is gone and the normal filters/results surface is visible again.
- If AMD exposes these filters through a filter drawer, modal, or side panel, apply the visible target filters there and then return to the plain jobs results surface.
- If one target is already reflected in active chips, checked options, visible field values, or URL state, treat that target as complete and move on.
- If a target filter is not exposed on the current AMD surface, record it as pending in `update_phase_memory` and continue with the filters that are available.
- After the visible AMD target filters are satisfied, write completed/pending filter facts into `update_phase_memory`.
- If the live page exposes a narrowed total such as `N jobs`, `Show N jobs`, or a pagination label such as `1 of N`, include structured `metrics` in `update_phase_memory` with `results_count`, `total_pages`, and `page_size` when visible.
- End `Job Filtering` only after the browser is back on the plain AMD jobs results surface, not while a filter drawer or modal is still open.

## Job Retrieval

- Record all reachable AMD jobs from the current filtered live jobs surface.
- Record the full current AMD results page before deciding whether to stop or paginate.
- Keep recording AMD results page by page until a site/project retrieval stop condition is met or the final reachable results page has been recorded.
- Do not stop early just because one current page contains enough jobs.
- Do not open individual job detail pages just to retrieve the current visible page list.

### AMD Results Surface Guidance

- Use the visible AMD jobs list or result-card region as the current page's job source.
- Treat detail panes, selected-job previews, recommendation sections, filter chrome, and account chrome as context only during retrieval.
- Do not reuse the same current results-page URL as the job URL for multiple visible roles.
- Prefer visible per-job links, card anchors, job title links, or href-bearing job elements that correspond to the current visible AMD results page.
- As soon as the current visible AMD results entries yield concrete per-job `{title, url}` pairs, immediately call `record_jobs`.
- Record lightweight list-level fields only when visible: title, url, location, posted label, employment type, match label, and apply state.
- If a current-page route successfully produces AMD job records, save a concise `retrieval_carry_forward` note with `Inspect First`, `Worked Shape`, `Ignore`, and `Ready To Record`.
- On the next AMD page, start from the carried route before trying unrelated extraction routes.

### Pagination

- After recording the current page, use a real visible next-page, numbered-page, or load-more control when available.
- Move sequentially page by page.
- Treat posted-date or posted-age filters as apply-candidate eligibility first, not as an immediate pagination stop.
- Do not stop AMD retrieval only because one or a few visible jobs on the current page are outside the preferred posted window.
- After `record_jobs` succeeds for the current AMD page, continue to the next page when the current page contains any in-window role, any `new` history match, or any `existing_needs_enrichment` result and a real next-page / load-more control is available.
- Stop AMD retrieval only after the current page has been recorded and one of these is true: the current visible page is entirely outside the preferred posted window on a clearly newest-first listing; `record_jobs` returns `stop_recommended = true` with no enrichment needed; or there is no real next-page / load-more control.
- If the current page mixes in-window and older roles, record the full page and then continue pagination when a real next-page / load-more control is available.
- If AMD shows a total count or page label that implies more results, re-check the jobs pagination/footer area before declaring retrieval complete.
- Finish only when the current page is recorded and no further real AMD next-page or load-more action is available.

## AMD Candidate Profile / Resume Update Handling

- Treat `Candidate Profile - <job>` pages inside `global-external-amd.icims.com/jobs/<job_id>/<slug>/candidate?mode=apply...` as a normal AMD apply step, not as an error.
- If an AMD apply step reaches `Candidate Profile`, update only the resume unless a required field is explicitly blocking progress.
- Use `Replace Resume`, `Upload Resume`, `My Computer`, or the closest visible AMD resume upload control to upload the staged resume PDF.
- AMD/iCIMS resume upload controls may expose a hidden `input[type=file]` that cannot be clicked normally. Do not click a hidden file input or keep retrying a pointer click when an outer iCIMS wrapper intercepts pointer events.
- When a file input is required, use the browser file upload / file chooser tool to set the staged resume PDF on that input, then re-read the live page before continuing.
- Do not proactively change name, phone, address, education, professional experience, or other profile fields.
- After the resume is selected or accepted, click `Update Profile` and treat it as AMD's safe forward action for the `Candidate Profile` apply step.
- If `Update Profile` reports a required-field error that cannot be answered from persona, resume, or visible facts, record the job as `blocked` with the visible reason.
- Do not enter AMD dashboard profile maintenance routes such as `questions?back=dashboard`, `candidate?back=dashboard`, or generic profile-update links just to continue search, review, retrieval, or apply.
- If a non-apply phase unexpectedly lands on an AMD apply `Candidate Profile - <job>` page, follow this section only if the current task is applying to that job; otherwise return to the phase target page or stop with `blocked`.
- Do not click accessibility skip links such as `Skip Branding`, `Skip to Main Content`, or `Skip Navigation`; they are not AMD business actions and can trap browser automation. Read and act on the real iCIMS content frame instead.

## Apply

### Matching

- Use the shared project matching rule for AMD; AMD does not override matching with a site-native match label.
- Open the saved AMD job URL, sync the current live JD and application state, then apply the project two-stage matching rule.
- If the project rule marks the job `filtered_out`, update the job and move to the next saved job.
- Continue into AMD apply only after the current job is `recommended_apply`.

### Apply Flow

- From a `recommended_apply` AMD job page, use the visible AMD apply entry action.
- Fill only required fields.
- For required email fields, use `ycalex1204@gmail.com`.
- On AMD iCIMS `Enter Your Information` pages, fill the `Email` field with `ycalex1204@gmail.com`, accept the required privacy notice checkbox such as `I accept`, and then use `Next`.
- If an apply step requires login, first use a visible `LinkedIn`, `Continue with LinkedIn`, remembered LinkedIn account, or equivalent one-click LinkedIn continuation.
- Do not click a public/header `Returning User Login` link during apply when it belongs to AMD search chrome, marketing chrome, or a generic account entry outside the current job's iCIMS apply flow.
- The only acceptable `Returning User Login` click during apply is a single click on the current job's iCIMS apply-login bridge, and only when it clearly belongs to the same job being applied to.
- After that single current-job apply-login bridge attempt, continue only if AMD reaches a normal apply form, a dashboard/past-submittal status for the same job, a `submit_apply` page, or an explicit submitted/already-applied state.
- If AMD reaches `login.icims.com/u/login/password`, a visible password field, MFA, verification code, CAPTCHA, email confirmation, or any human-only login challenge with no visible LinkedIn continuation, record the current job as `blocked` with `apply_state = blocked_login_password_required`, then move to the next saved job.
- Do not loop through AMD identifier/email/password login screens for the same job.
- Do not mark AMD apply as blocked only because a passive `Protected by hCaptcha` notice is visible; continue while the normal required fields and `Next` action remain usable.
- When AMD reaches `Candidate Profile` or resume upload/update UI, follow `AMD Candidate Profile / Resume Update Handling`.
- Accept required AMD acknowledgements, policy consents, or terms checkboxes when they are required to proceed.
- Use AMD's visible safe forward action such as `Next`, `Continue`, `Review`, `Save and continue`, or apply-step `Update Profile` to move through multi-page forms.
- Do not treat clicking `Next`, `Continue`, or `Submit` as completion by itself; always re-read the fresh live page.
- On the final confirmation page for a `recommended_apply` job, use the final submit action.

### Submission Signal

- Treat the AMD job as `submitted` only after AMD shows an explicit application-success confirmation on the live page.
- Treat confirmation text such as `Your application was submitted successfully. Thank you for applying.` or a close equivalent as an explicit AMD application-success confirmation and record the current job as `submitted`.
- If AMD shows the role was already applied to, record `already_applied`.
- If final submit outcome is ambiguous, record the job as `blocked` with the visible reason.
