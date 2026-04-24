---
id: site-qualcomm
name: qualcomm Site Skill
version: v1
updated_at: '2026-04-21'
scope: site
site_key: qualcomm
status: ready
apply_enabled: true
---
# Qualcomm Site Skill

## Session Preparation

### Goal

- Complete Qualcomm candidate login, upload the staged resume into the candidate profile's resume manager, and leave the browser ready to continue into Qualcomm job search.

### Site Facts

- Use the Qualcomm candidate / careers flow, not a generic corporate marketing page, as the working surface.
- Qualcomm sign-in should use the visible Google account continuation when that option is available on the live page.
- After Qualcomm login, the flow may land on a candidate dashboard, profile area, or account surface before returning to jobs search.
- The required Qualcomm resume step is specifically `Resume Manager`.

### Workflow

- If the current Qualcomm page still shows a visible Google sign-in continuation for the jobs or candidate flow, continue that sign-in path instead of declaring readiness.
- After login, use the Qualcomm header avatar / account-name dropdown, then choose the dropdown's `Profile` path from there.
- On the dropdown-driven `Profile` page, the first required setup target is `Resume Manager`.
- Before the Qualcomm resume step is satisfied, ignore the rest of `Profile`; do not inspect `About`, `Skills`, `Experience`, `Education`, or other profile sections first.
- If `Resume Manager` is visible and the Qualcomm resume step is not yet satisfied in this run, click `Resume Manager` immediately.
- If `Resume Manager` is not yet visible, stay on that same `Profile` page and keep rechecking only for `Resume Manager`.
- In `Resume Manager`, check whether the current staged `cv.pdf` is visibly present.
- If the current staged `cv.pdf` is not visibly present, upload it there.
- Treat the Qualcomm resume step as satisfied the moment the current staged `cv.pdf` is visibly present in `Resume Manager`.
- As soon as that Qualcomm resume step is satisfied, write it into `update_phase_memory` and mark `Resume Manager` as do-not-repeat for the current unchanged setup state.
- Once the Qualcomm resume step is satisfied, stop treating `Resume Manager` as the next target.
- If the resume dialog is still open after the Qualcomm resume step is satisfied, close it.
- After the Qualcomm resume step is satisfied and the dialog is closed, the next action is `Search for Jobs`.
- Do not reopen `Resume Manager` again in this same `session_preparation` run after `cv.pdf` has already been confirmed there.
- Leave the browser on the Qualcomm jobs-search surface. Only then is `Session Preparation` complete.

### Completion Or Blocked

- End `Session Preparation` only after Qualcomm is signed in, the current staged `cv.pdf` is visibly present in `Resume Manager`, and the browser has returned to `Search for Jobs` or the searchable Qualcomm jobs surface.
- If the flow reaches password entry, MFA, verification code, CAPTCHA, email confirmation, or another explicit human-only challenge with no visible one-click Google continuation left, stop with `blocked`.

### Don't

- Do not stop on a Qualcomm candidate home, profile page, or resume manager page if the flow has not yet returned to jobs search.
- Do not use the homepage-level profile card or other broad profile shortcut when the header avatar/account dropdown is available.
- Do not treat the `Profile` heading by itself as completion; `Resume Manager` still must be opened.
- Do not keep observing or editing general Qualcomm profile fields such as About, Skills, or Experience before `Resume Manager` is satisfied.
- Do not keep observing the `Profile` page after `Resume Manager` is visible and the Qualcomm resume step is not yet satisfied; click it immediately.
- Do not click `Export as resume` before `Resume Manager`.
- Do not scroll through or inspect the rest of `Profile` before clicking `Resume Manager`.
- Do not substitute another resume area, profile section, or settings page for `Resume Manager`.
- Do not substitute another navigation shortcut for `Search for Jobs` during this setup flow.
- Do not upload a different file when the current staged `cv.pdf` is already visibly present in the Qualcomm resume manager.
- Do not reopen `Resume Manager` after `cv.pdf` has already been confirmed there in the current `session_preparation` run.

## Channel Discovery

### Navigation

- From the signed-in Qualcomm candidate flow, use `Search for Jobs` or the closest visible jobs-search entry to reach the real searchable Qualcomm jobs surface.
- If Qualcomm opens a candidate dashboard or profile area first, stay inside the Qualcomm candidate flow and continue from there into jobs search instead of drifting into unrelated site navigation.
- If the current page already shows Qualcomm job search controls, real result cards, or a searchable jobs list, stop discovery immediately.

### Success Signal

- Treat discovery as complete only after a real Qualcomm searchable jobs UI or visible jobs results list is available.

## Job Filtering

### Filtering Goal

- Stay on the current Qualcomm jobs surface and apply these Qualcomm targets when the page exposes them: `Location = China`, `Job Category / Function = Software Engineering`, `Machine Learning Engineering`, and `Software Applications Engineering`.

### Filtering Direction

- Use `Location = China`.
- If `View all jobs` or another Qualcomm jobs action removes the current China location state, restore `Location = China` before spending more work on category filters.
- In the Qualcomm role, profession, category, or function filter, keep all visible target categories that match `Software Engineering`, `Machine Learning Engineering`, and `Software Applications Engineering`.
- When one of those three target category checkboxes is visible and not yet selected, click it directly.
- Do not stall on repeated checkbox inspection when those visible target category checkboxes are still unchecked; use direct selection actions first.
- Do not stop after selecting only one of those Qualcomm target categories if other target categories from that same set are also visible in the current filter surface.
- If Qualcomm already shows active chips, checked options, or URL state for one of those target dimensions, reuse that state and continue.
- If one of the three target categories is not exposed on the current Qualcomm page, apply the remaining visible target categories and continue.
- After the Qualcomm target family set is clearly selected, record that in `update_phase_memory` and do not reopen `All filters` only to re-check the same family set on the same unchanged page.
- If the Qualcomm target family set is already complete but `Location = China` is still missing, record that pending split in `update_phase_memory` and go back to the top location control instead of reopening `All filters`.
- Once the Qualcomm location target and the Qualcomm target family set are satisfied, leave the filters surface before ending `Job Filtering`.
- If `All Filters` is still open, first use its own forward action such as `Show N jobs`; if that forward action is not available, close `All Filters`.
- End `Job Filtering` only after the browser is back on the plain Qualcomm jobs results surface, not while the `All Filters` dialog is still open.

## Job Retrieval

- Record Qualcomm jobs from the current filtered live jobs surface.
- Record results page by page until Qualcomm shows no further real next-page or load-more action.
- Read each job from the job card shown on the current results page.
- Required fields are `title` and `url`.
- If the same job card also shows them, include `location` and `posted_label`.
- Use the job URL shown on that same job card.
- For Qualcomm, the reusable successful read path is the current results page's job-card links: the cards are link/anchor elements with real per-job `href` values, even when they are exposed with `View job:` button-like labels.
- Write Qualcomm `retrieval_carry_forward` so `Worked Shape` preserves that link/anchor + same-card `href` shape; do not summarize it as generic buttons.
- Do not use job-alert panels, filter chrome, or account/header content as the results list container.
- Do not use the right-side preview to represent the whole results page.
- Do not use a single selected role's apply link as the URL for multiple jobs.
- Do not open job detail pages just to add more fields during retrieval.
- Once the current page's job cards have usable `title + url` records, call `record_jobs`.
- After a Qualcomm page is recorded, write concise `retrieval_carry_forward` memory for that successful job-card-link read before moving to the next page.
- On the next Qualcomm results page, first reuse that remembered job-card-link read on the current live page before trying a different route.

## Apply

### Matching Override

- Open the Qualcomm job URL first, then use the current live page's Qualcomm match signal as the application gate.
- Only apply to Qualcomm roles labeled `Strong Match` or `Good Match`.
- After opening the Qualcomm job URL, first make sure the actual job content has appeared on that same job page. A header-only shell, `Single Position` shell, generic welcome text, or page without the job's match label / apply entry / real job details is not ready for a match decision.
- If the job page is not ready yet, stay on that same job URL and re-read the live page; do not mark the job `filtered_out`, do not mark it failed, and do not move to another job from a header-only or partial page.
- Make the match decision from the job detail page or the first apply-entry page where the Qualcomm match signal is visible.
- When a visible Qualcomm match signal is `Strong Match` or `Good Match`, use it as the decision to continue the live application flow.
- On a Qualcomm job-detail page, if `Strong Match` or `Good Match` is visible and the same live page also shows `Apply Now`, click that same page's `Apply Now` before writing any non-terminal `recommended_apply` / `ready_to_start_apply` update.
- Do not call `update_jobs` only to record `recommended_apply` while `Apply Now` is already available on the same live page; that intermediate record can wait until the job reaches a real terminal result.
- When the current Qualcomm job reaches `submitted`, `already_applied`, `filtered_out`, or `blocked`, write one final `update_jobs` record that includes the observed `match_label`, decision, terminal application status, and any confirmed job id / job number.
- If the current Qualcomm role does not show a clear `Strong Match` or `Good Match` signal at that decision point, mark it `filtered_out` and move on.
- Once the current Qualcomm job has already been marked `recommended_apply` from a visible `Strong Match` or `Good Match` signal, do not reverse it to `filtered_out` later just because a deeper apply form page no longer shows the match label in the current snapshot.
- After `recommended_apply` is established for the current Qualcomm job, the remaining apply pages are form-completion and submission pages, not a new match-gating step.
- Do not fall back to the shared project matching rule for Qualcomm when the live Qualcomm match signal is absent, unclear, or weaker than `Good Match`.

### Form Filling

- Before continuing, check the Qualcomm name fields. Keep `First Name = xinpeng` and `Last Name = li`; if the live page shows different values in required name fields, correct them.
- Fill only required Qualcomm fields. Do not spend time changing optional fields that are already acceptable.
- For country or region questions, use `China`.
- For candidate city or current city questions, use `Taiyuan`.
- For work authorization or legal right to work in China questions, answer `Yes`.
- If the Qualcomm page already shows an acceptable value in a required field, keep it and move on instead of rewriting it.
- After entering the Qualcomm apply form for a `recommended_apply` job, first satisfy visible required fields and visible validation errors, then continue toward the final submit step.
- Do not treat a click on `Submit application` as completion by itself.
- After any final submit click, immediately re-read the fresh live page and verify whether Qualcomm shows an explicit application-success confirmation.
- If the form is still open, the page still shows required fields, or Qualcomm shows validation errors after the submit click, the current job is not finished yet; keep working that same job instead of moving on.

### Site Signals

- If Qualcomm clearly shows that the current role has already been applied to, record it as `already_applied` instead of submitting again.
- Treat the current Qualcomm job as submitted only after the final submit action leads to an explicit Qualcomm application-success confirmation on the live page.

### Escalation

- Stop if Qualcomm asks for required information that cannot be answered from the current live page, lightweight apply facts, or requested context bundles.
- Stop if the final Qualcomm submit outcome is ambiguous.
