---
id: site-apple
name: Apple Site Skill
version: v1
updated_at: '2026-06-03'
scope: site
site_key: apple
status: ready
apply_enabled: true
---
# Apple Site Skill

## Site Policy

### Retrieval Policy

- Apple retrieval should use the accepted China/software/newest/team-filter strategy unless a later accepted evolution run replaces it.
- Record the current visible Apple results page before opening job details or deciding whether to stop.
- Use the project-level pagination/history stop policy after the current page is recorded, including the required confirmation page when a real next-page control exists.
- Treat Apple internships and new-grad roles as hard exclusions even when they appear inside otherwise valid result pages.

### Application Review Policy

- Apple application review requires a signed-in Apple Careers profile surface.
- Treat `Submissions -> Active` as realtime and inspect every reachable Active page.
- Treat `Submissions -> Archived` as the project-level inactive/historical area. Inspect it once after `Active`; then apply the project-level historical pagination and coverage policy.

## Matching Policy

### Application Gate

- Use the project common matching rule unless Apple exposes a clearer site-native application state.
- Do not apply to Apple internships, campus, student, 2026 New Grad, new-grad, co-op, 校招, or 实习 roles.
- Before clicking `Submit Resume`, re-check the live title, role page, JD, and Apple role number.
- If a required application fact is not available from local profile/persona/CV context, mark the job `blocked` instead of guessing.

## Session Preparation

### Goal

- Prepare Apple Careers for the requested test scope without starting an application during this phase.
- Use the Apple Careers jobs system at the URL stored in `site.json`.
- Public job retrieval is allowed without login only when `apply_enabled` is false or the current task is explicitly retrieval-only.
- When `apply_enabled` is true or the user requested application submission, this phase must establish a signed-in Apple Careers profile/account surface before later retrieval or apply phases continue.

### Authentication

- Apple Careers exposes `Profile` and `Sign In` from the jobs header.
- If a signed-in profile surface is already visible, treat session preparation as complete.
- If `apply_enabled` is false and the current task is public job discovery, filtering, or retrieval, the Apple search page is enough and you should not click `Profile` or `Sign In`.
- If `apply_enabled` is true or the current task requests applying, public search is not a sufficient ready signal. Click `Profile` or `Sign In` and confirm the user is signed in before continuing.
- Use the visible Apple Careers `Sign In` path when the current task requires application-status review from a signed-in profile or any future apply behavior.
- Do not type or invent Apple ID credentials.
- If Apple ID sign-in shows a filled account field, a saved/autofilled password state, and the only required action is clicking `Continue`, `Sign In`, or the equivalent next button, click that button once to complete the saved-credential login flow.
- If the user explicitly says Apple credentials are already saved or the page only needs `Continue`/next click, trust that instruction for this run and click `Continue` once before declaring `blocked`.
- After clicking `Continue` once, wait for either signed-in Apple Careers evidence or the next human-only challenge.
- Stop with `blocked` when the password is missing, the page requires manual password entry, or the flow reaches MFA, verification code, CAPTCHA, device approval, passkey approval, or another human-only challenge.

### Ready Signal

- Treat `Session Preparation` as complete when Apple Careers shows a signed-in profile surface, profile information, saved roles, submitted applications, or another obvious account-ready state.
- For public retrieval-only testing with apply disabled, also treat the Apple Careers search surface as session-ready when it shows `Find your perfect role`, `Search by role or keyword`, `Filters`, `Search Results`, or a visible Apple job list.
- For apply-enabled runs, do not treat public search results as session-ready. The ready signal must be signed-in profile/account evidence.
- Public search results do not prove login, but they are enough to continue to public retrieval when apply is disabled.
- If login is blocked by missing credentials or a human-only Apple ID challenge, record the blocked reason and stop the phase.

### Don't

- Do not start a new application during `Session Preparation`.
- Do not click `Sign In` or `Profile` just to retrieve public Apple jobs when apply is disabled.
- Do not continue an apply-enabled run from public search without signed-in profile/account evidence.
- Do not upload or edit resume/profile details unless a later Apple apply skill is explicitly approved.
- Do not treat public job search as proof of login for status review or apply.

## Application Status Review

### Goal

- Check whether Apple Careers exposes a signed-in profile/application area, but do not invent application statuses when the dashboard is not visible.

### Workflow

- If the page is not signed in and only public `Profile` or `Sign In` links are visible during apply-disabled retrieval-only testing, do not click them. Record or state that no signed-in application dashboard is available, then end `Application Status Review` with `done`.
- If `apply_enabled` is true or the current task requests applying and only public `Profile` or `Sign In` links are visible, the previous login preparation is not complete. Open the sign-in/profile path if possible; if Apple requires password, MFA, verification, CAPTCHA, device approval, passkey approval, or another human-only challenge, stop with `blocked` and ask for user takeover.
- For apply-enabled runs, do not end `Application Status Review` as `done` from a public-only search page unless a signed-in profile/account surface is visible or the phase records a precise blocked reason.
- From a clearly signed-in Apple Careers page, open `Profile`.
- On Apple's `Your Roles` page (`/app/en-us/profile/roles`), treat `Submissions` as one submitted-application review surface:
  - Prefer the currently visible submitted-role list. Record visible rows from the current `Submissions` surface before any tab or pagination action.
  - `Active` maps to the primary live area and `Archived` maps to the inactive/historical area. Inspect each reachable area once, without switching back to an already-inspected area.
  - If the current `Submissions` surface has no application rows, treat it as complete when there is no usable pagination or load-more control visible for that same surface.
  - Do not call `record_application_reviews` with an empty list until the current visible `Submissions` surface has been inspected and is empty or unavailable.
- Look for visible application, submitted role, saved role, recently viewed role, or profile application-history surfaces.
- If Apple shows an application list, record each visible application row with the best available title, URL, role number, visible raw status, and normalized `application_review_status`.
- If Apple only shows saved roles, recently viewed roles, profile info, or no application-history area, record that no reliable application-status dashboard was found and end `Application Status Review` with `done`.
- If Apple returns to sign-in or Apple ID challenge, stop with `blocked`.

### Status Mapping

- Use `active` for visible submitted/in-review/in-process application rows with no clearer terminal status.
- Use `rejected`, `closed`, or `withdrawn` only when Apple clearly shows that meaning.
- Use `unknown` when a row is visible but the status cannot be safely interpreted.

### Completion Or Blocked

- On `/app/en-us/profile/roles`, the phase is complete only after each reachable `Submissions` area (`Active` and `Archived`) has been inspected and recorded or found empty with no same-area pagination remaining.
- If no Apple submitted applications are visible in any required area and no pagination remains, recording zero application reviews is a valid completion. After recording zero reviews, immediately call `phase_result` with `status=done`.
- Do not loop through saved roles, recently viewed roles, or profile tabs when no submitted-application list is visible.
- Do not click `Active` and `Archived` repeatedly after zero visible application rows have already been established.
- After recording one `Submissions` area, continue to the other required uninspected area. After both are complete, call `phase_result` with `status=done`.
- Do not create history rows for Apple dashboard records that are not already in local history; record them through application review only.

## Channel Discovery

### Navigation

- Start from `https://jobs.apple.com/en-us/search` or the current Apple Careers entry URL in `site.json`.
- If the page shows `Find your perfect role`, `Search by role or keyword`, `Filters`, `Search Results`, or a visible Apple job list, channel discovery is complete.
- If the page opens on a generic Apple Careers marketing page, use the visible `Search` or jobs-search entry to reach `/en-us/search`.
- Stay inside `jobs.apple.com`.

### Success Signal

- Treat discovery as complete only when the Apple Careers search UI or real job results are visible.
- Apple job result cards normally expose title, team, posted date, location, role number, and a full-role-description link.

### Stop Conditions

- Stop with `blocked` if Apple Careers cannot load a searchable jobs surface after refresh and one alternate navigation attempt.

## Job Filtering

### Filtering Goal

- Apply the project job target on Apple Careers: China location, software/AI-oriented roles, full-time/non-intern where visible.

### Immediate Success Check

- Before clicking any filter control, inspect the current URL, page title, active filter labels, search box value, sort control, and visible results.
- If the current page URL contains `location=china-CHNC`, `sort=newest`, `search=software+engineer`, and the accepted Apple `team=` filter list below, treat `Job Filtering` as complete immediately.
- If the current page only contains `location=china-CHNC`, `sort=newest`, and `search=software+engineer` without the accepted `team=` filter list, do not treat it as the final Apple filtering strategy. Navigate once to the accepted team-filter URL below.
- On that already-filtered page, record a concise phase memory note with the URL, visible result count or page count if available, and any missing filter dimensions, then finish the phase with `done`.
- After the accepted team-filter URL is loaded and visible results are present, call `phase_result` with `status=done` immediately. Do not perform a second observation, do not reopen filters, and do not keep reasoning inside `Job Filtering`.
- Do not reopen `Filters`, location controls, sort popovers, or keyword controls after this success check passes.
- If the current page has `location=china-CHNC` and `sort=newest` but no keyword, prefer direct navigation to the complete Apple filtering URL below instead of opening `Filters`.

### Filtering Direction

- At the start of Apple `Job Filtering`, navigate directly to the accepted China/software/newest/team-filter Apple search URL before using any visible filters:
  `https://jobs.apple.com/en-us/search?search=software+engineer&sort=newest&location=china-CHNC&team=machine-learning-infrastructure-MLAI-MLI+deep-learning-and-reinforcement-learning-MLAI-DLRL+natural-language-processing-and-speech-technologies-MLAI-NLP+computer-vision-MLAI-CV+applied-research-MLAI-AR+apps-and-frameworks-SFTWR-AF+cloud-and-infrastructure-SFTWR-CLD+core-operating-systems-SFTWR-COS+devops-and-site-reliability-SFTWR-DSR+engineering-project-management-SFTWR-EPM+information-systems-and-technology-SFTWR-ISTECH+machine-learning-and-ai-SFTWR-MCHLN+security-and-privacy-SFTWR-SEC+software-quality-automation-and-tools-SFTWR-SQAT+wireless-software-SFTWR-WSFT`
- This accepted strategy came from successful run `job_batch_a8d9b30006` and was confirmed by the user. It is the default Apple retrieval strategy until a later evolution run replaces it.
- Do not remain on Apple's default United States results page. A URL or visible filter state containing `location=united-states-USA` is not acceptable for Apple retrieval.
- After direct navigation, confirm that the page shows `China`, the `software engineer` keyword, visible results, `Sort by: Newest`, and either the accepted `team=` query string or visible team filtering.
- If the accepted direct URL is loaded and shows a usable results surface, do not reopen the location filter, sort popover, keyword control, or generic `Filters` panel.
- If direct navigation fails, then use Apple `Location` filter = `China` and choose `Newest` from the sort control.
- Use Apple `Keyword` search for the project target, such as `software engineer`, `machine learning`, `AI`, `full stack`, or the closest project-level role keyword.
- After a keyword is submitted, Apple may reset sorting to relevance. If this happens, set `Sort by` back to `Newest` once, then re-run the immediate success check.
- If Apple `Teams` exposes relevant categories, prefer `Software and Services` and `Machine Learning and AI` when visible. The accepted team slug set is:
  - `machine-learning-infrastructure-MLAI-MLI`
  - `deep-learning-and-reinforcement-learning-MLAI-DLRL`
  - `natural-language-processing-and-speech-technologies-MLAI-NLP`
  - `computer-vision-MLAI-CV`
  - `applied-research-MLAI-AR`
  - `apps-and-frameworks-SFTWR-AF`
  - `cloud-and-infrastructure-SFTWR-CLD`
  - `core-operating-systems-SFTWR-COS`
  - `devops-and-site-reliability-SFTWR-DSR`
  - `engineering-project-management-SFTWR-EPM`
  - `information-systems-and-technology-SFTWR-ISTECH`
  - `machine-learning-and-ai-SFTWR-MCHLN`
  - `security-and-privacy-SFTWR-SEC`
  - `software-quality-automation-and-tools-SFTWR-SQAT`
  - `wireless-software-SFTWR-WSFT`
- Do not spend unlimited time trying to force a full-time filter if Apple does not expose one on the search surface.
- Do not click the generic `Filters` button just to search for full-time or extra narrowing after direct navigation has produced the accepted Apple team-filter results page. This repeated action has previously caused no-progress loops on Apple.
- The plain `China + software engineer + newest` URL without `team=` is a fallback exploration surface, not the final accepted Apple filtering strategy.
- Exclude internships and early-career roles through project-level filtering and retrieval decisions if the live page title or JD says `intern`, `internship`, `campus`, `student`, `2026 New Grad`, `new grad`, `new graduate`, `co-op`, `校招`, or `实习`.

### Completion

- End `Job Filtering` once China and newest sorting are active, the accepted team-filter URL is loaded, and visible search results are present.
- For Apple, `location=china-CHNC` + `search=software+engineer` + `sort=newest` + the accepted `team=` filter list + visible search results is enough for retrieval. Missing full-time filtering should be handled during `Job Retrieval`, not by continuing to click filters.
- If this exact accepted state is already visible, `Job Filtering` is complete even if there are additional optional filters or missing full-time controls. Write at most one phase memory note, then immediately finish with `phase_result done`.
- If Apple exposes a results count such as `N Result(s)`, write it into phase memory when visible.
- End on the plain Apple results surface, not while a filter accordion, typeahead list, or sort popover is still open.

## Job Retrieval

### Retrieval Goal

- Record Apple jobs from the current filtered Apple Careers results page.
- This Apple skill has graduated from first-draft retrieval testing. When the active run requests applications, retrieval should feed the later apply phase instead of stopping as retrieval-only.

### Result Shape

- Use the visible Apple job list under `Search Results`.
- For each job card, record:
  - `title` from the job title link
  - `url` from the full role/details link, normalized to `https://jobs.apple.com/...`
  - `location` from the card's location field
  - `posted_label` from the visible posted date
  - `site_job_id` from `Role Number` when visible, such as `200665350-0351`
  - `team` when visible, such as `Machine Learning and AI` or `Software and Services`
- Apple also exposes structured page data with `postingTitle`, `postingDate`, `postDateInGMT`, `reqId`, `locations`, `team.teamName`, and `jobSummary`; use those fields when visible in the browser context or page text.

### Recording Rules

- Record the current visible results page before opening any individual job detail.
- Do not use saved roles, favorites, recently viewed roles, filter chrome, or profile chrome as job records.
- Do not use a single `Submit Resume` link as the URL for multiple jobs.
- Prefer the `See full role description` or title link as the job URL.
- If the current page yields concrete `{title, url}` records, call `record_jobs` immediately.
- If a result has visible `Role Number`, use it as `site_job_id`.

### Stop Conditions

- Apple retrieval should follow the project-level pagination and history stop policy. Do not stop merely because the current page plus one next page has been recorded.
- If a visible Apple job is older than the project date window and results are sorted newest first, finish recording only in-window jobs from the current page and stop retrieval.
- If the page shows no results for China plus project keywords, record a no-results phase memory and end `Job Retrieval`.
- If pagination or next-page links are visible and no project-level or site-level stop condition has triggered, move sequentially after recording the current page.

## Apply

### Apply Enabled

- `apply_enabled` is true for Apple.
- Apply only to Apple jobs that passed the project-level role/date/intern filtering and are not already recorded as submitted, already applied, rejected, withdrawn, closed, blocked, or apply failed.
- Use the accepted Apple retrieval strategy from `job_batch_a8d9b30006` as the source of candidate jobs unless a later accepted evolution strategy replaces it.
- Apple `2026 New Grad` roles are excluded. If the live title, role page, or JD shows `2026 New Grad`, record `filtered_out` and move to the next job without entering the apply flow.

### Apply Workflow

- Open the Apple job detail page from the recorded job URL.
- Re-check title, location, posted date, role number, and JD before starting the application.
- If the job detail page shows an already-applied, submitted, saved-only, unavailable, closed, or no-longer-accepting-applications state, record the correct application status and do not apply.
- If the page shows `Submit Resume` or an equivalent Apple apply entry, click it only after the job has passed matching checks.
- If Apple asks for resume/profile selection or upload and a current resume is needed, use the current exported resume from the workspace.
- For routine application-form facts, use `workspace/profile/application_profile.md` through the project-level apply rules. Do not infer reusable form facts from other site skills during the live Apple apply flow.
- On Apple's `Self-Disclosure` step, use `Gender = Male` from `application_profile.md` when a gender field is required. Do not select `Prefer Not To Disclose` when `Male` is available as a visible option.
- For standard profile, authorization, demographic, compliance, or job-specific questions, answer from `application_profile.md`, `persona.md`, the current CV, and project-level rules. Do not guess facts that are not present.
- If a required question cannot be answered safely from local facts, stop that job as `blocked` and continue to the next job.

### Apple Profile Information Step

- Treat Apple's `Profile Information` step (`stepName=resumeParsedinfo`) as a required transactional subflow for the current job. Once this step is reached, do not navigate to another job, do not start another application, and do not end the current apply attempt until the step either advances or the current job is explicitly recorded as blocked.
- Required reusable address values come from `workspace/profile/application_profile.md`: `State/Province = Shanxi`, `City/Town = Taiyuan`, and `Zip/Postal Code = 030000`.
- Fill all three address fields before clicking `Continue`. Do not click `Continue` repeatedly before the required fields are filled.
- Treat these fields as possibly controlled inputs or dropdown-backed fields, not guaranteed plain textboxes. Use this order:
  - Click the field and type or fill the value.
  - If a dropdown or suggestion appears, select the matching visible option.
  - Press `Tab` or otherwise blur the field.
  - Verify the value remains visible or retained before moving to the next field.
- After all three values are retained, immediately click `Continue`. Do not call `update_phase_memory` as the terminal action for this step, and do not switch to the next job before trying to advance.
- If Apple remains on `Profile Information` after clicking `Continue`, inspect visible validation errors and retry only the affected address fields once using the alternate interaction style: use slow typing instead of direct fill, or select the matching visible option if a dropdown appears.
- If the retry still leaves the current job on `Profile Information`, call `update_jobs` for the current job with `application_status=blocked`, `apply_state=blocked_form_validation`, and a precise `last_apply_error` describing the visible validation problem. Then continue to the next eligible job.
- Do not ask the user again for `State/Province`, `City/Town`, or `Zip/Postal Code`; those values are already available from the application profile.

### Completion Signal

- Treat the application as submitted only after Apple shows a clear success/confirmation state such as submitted application, application received, thank you for applying, confirmation page, or an equivalent final submitted status.
- After a confirmed submit, record `application_status=submitted` with title, URL, site job id when visible, and the confirmation text if available.

### Human Takeover And Safety

- If Apple asks for Apple ID password, MFA, verification code, CAPTCHA, device approval, passkey approval, or another human-only challenge, stop with `blocked` and ask for user takeover.
- Do not invent answers for work authorization, visa, legal, education, employment history, or job-specific experience.
- Do not retry final submit repeatedly if the page does not advance; record the visible state and block or continue based on evidence.
