---
id: site-deepseek
name: deepseek Site Skill
version: v1
updated_at: '2026-07-04'
scope: site
site_key: deepseek
status: ready
apply_enabled: true
job_identity:
  fragment_job_route_patterns:
  - '#/job/{site_job_id}'
  - '#/jobs/{site_job_id}'
  application_review_fallback:
    unique_title: true
    when_review_has_no_real_job_url: true
---
# DeepSeek Site Skill

DeepSeek currently uses a Moka/High-Flyer recruiting surface.

## Site Policy

### Retrieval Policy

- In `job_filtering`, use only the visible `职能类型` options that directly match the target engineering surface.
- Required DeepSeek/Moka target labels: `全栈开发/算法`, `AI核心系统研发`, and `模型数据策略`.
- Accepted DeepSeek/Moka filtering strategy: at the start of `job_filtering`, navigate directly to `https://app.mokahr.com/social-recruitment/high-flyer/140576#/jobs?zhineng%5B0%5D=16851&zhineng%5B1%5D=16428&zhineng%5B2%5D=16431&page=1&anchorName=jobsList`, wait for any `数据读取中` loading state to settle, verify the jobs list is visible, then finish `job_filtering` as done.
- Treat that direct hash-route filter URL as the proven strategy from prior successful evidence for `全栈开发/算法`, `AI核心系统研发`, and `模型数据策略`.
- During normal DeepSeek filtering, do not click the `职能类型` dropdown, arrow, or input after the direct filtered URL is available, because the live Moka filter control can intercept pointer events and cause no-progress failures.
- If the direct filtered URL fails to load any jobs list, make at most one visible filter-click fallback attempt, then continue to `job_retrieval` with the best stable visible jobs surface instead of looping in filters.
- `AI核心系统研发` is a mandatory target category for DeepSeek filtering. If it is visible and selectable, select it before leaving `job_filtering`.
- `模型数据策略` is also a target category when it covers AI/data strategy, model data, agent evaluation, search/data, or AI product/engineering-adjacent roles.
- If the DeepSeek/Moka filter supports multiple selections, select `全栈开发/算法`, `AI核心系统研发`, and `模型数据策略`, then stop `job_filtering` as soon as the filtered jobs list is visible.
- Do not keep opening or toggling `职能类型` after all visible required target filters are selected.
- Do not select broader or adjacent departments such as `运维`, `产品部门`, `深度学习研究员`, legal, operations, or other non-target groups merely to increase result count.
- If either required target label is not visible or cannot be clicked reliably, record which label was unavailable in phase memory, stop optimizing filters, and continue to `job_retrieval` with the best stable visible jobs surface; let retrieval/apply perform JD-level matching instead of looping in filters.
- If the filter does not support multiple selections, run or preserve retrieval coverage for the visible target option that can be applied, then continue to retrieval instead of treating one option as a replacement for the other.
- Treat AI/core engineering and model-data strategy keywords as high-priority DeepSeek retrieval targets: `Agent`, `深度学习`, `搜索算法`, `预训练`, `核心系统`, `AI超算`, `Harness`, `全栈`, `AI核心研发`, `模型数据策略`, `数据策略`, `模型数据`, `AI跨界人才`.
- Do not lose high-priority AI/core roles just because they appear on the hot-jobs surface before the filtered jobs-list page.
- Treat DeepSeek/Moka job detail URLs with `#/job/<uuid>` as real job URLs.
- Record the hash-route UUID from `#/job/<uuid>` as `site_job_id`.
- Do not collapse job detail URLs to the company recruitment entry URL.
- Record each visible job card with title, location when visible, full hash-route URL, and `site_job_id`.
- If only the company jobs-list route such as `#/jobs?...` is visible, open the job detail before recording the URL.

### Application Review Policy

- DeepSeek/Moka application-review pages may show an application status and title without the same `#/job/<uuid>` used by job-list/detail pages.
- DeepSeek runs in CareerEng target social recruitment roles only. During `Application Status Review`, inspect the `社招` application records only.
- Do not inspect, switch to, or record `校招` / campus application records during normal DeepSeek runs.
- If the candidate applications page opens on `校招`, switch once to `社招`; if `社招` is already visible, stay there.
- After all visible `社招` application rows have been recorded through `record_application_reviews`, immediately finish `Application Status Review` as done. Do not revisit `校招`, re-open already reviewed application rows, or repeat the same `社招` list.
- In application review, record the visible title, raw status, canonical review status, and review URL when present.
- If the review page has no reliable `#/job/<uuid>` job URL, CareerEng may merge the review state to an existing DeepSeek job by same-site unique title only. If the title is duplicated, treat it as ambiguous and do not auto-merge.
- Treat active review states such as `初筛`, resume review, application received, in review, in process, or similar as an active/submitted application state for apply planning; such jobs should be skipped by normal apply list generation unless the site clearly exposes a resumable unfinished application.
- If the logged-in page exposes submitted, active, inactive, rejected, closed, or withdrawn applications, record the visible raw status and canonical status.

### DeepSeek / Moka Apply-Page Closure

- When a DeepSeek job reaches a Moka `#/job/<id>/apply` page, keep that same job exclusively active until one terminal `update_jobs` result is written for that exact job.
- Do not leave an entered apply flow unclosed. Before switching jobs, restarting bootstrap, or abandoning the page, convert the current job into one of: submitted/already_applied/closed, blocked with a specific reason, or apply_failed with a specific runtime reason.
- Treat successful resume upload as a mandatory closure checkpoint, not as permission to move on. Immediately re-read the same live `/apply` page after upload. If the first read is sparse, take exactly one more same-page observation before deciding the next step.
- After upload or any other page-changing action, use the freshest same-job page state to choose the next branch:
  - visible submitted / success / duplicate / already-applied / closed text -> record the matching terminal status immediately;
  - visible required field / consent checkbox / validation message / continue / next / review / submit / confirm / save control -> act on that visible requirement and stay on the same job;
  - no visible progress control, no visible missing requirement, and no visible terminal text -> record a terminal blocker with a concrete reason such as `post_upload_apply_shell_without_progress_control`, and include the current URL plus the visible controls or lack of controls.
- If browser/session/runtime instability happens after `/apply` entry and prevents the required fresh observation or visible next step, do not return to the outer apply loop without closing the item. Record a same-job terminal blocked/apply_failed outcome with the interruption reason and current URL.
- A live apply interruption is not grounds to silently resume browsing other jobs. The apply item must be terminalized first, even when the reason is runtime recovery exhaustion or browser-in-use/session instability.
- Prefer concrete blocker reasons over generic loop escape. If the form shell is visible but offers no actionable next control after re-observation, write the blocker for that shell state instead of leaving the item unclosed.
## Matching Policy

### Application Gate

- Use the project common matching rule unless DeepSeek exposes a clearer site-native decision signal.
- Hard-exclude pure intern, campus, student, new-grad, co-op, 校招, 应届, and internship-only roles.
- Do not treat mixed employment labels such as `全职/实习` or `实习/全职` as terminal hard exclusions by themselves. For target AI/core roles, evaluate the JD/CV match normally and use the full-time path when the live application flow offers one.

## Session Preparation

### Authentication

- For the first DeepSeek run, require manual login/user takeover when the Moka page asks for account, password, verification, CAPTCHA, or other human-only input.
- After the user completes login, continue from the same browser session.

### Ready Signal

- The ready state is the Moka/DeepSeek jobs surface or candidate surface loaded without a blocking login challenge.

## Channel Discovery

### Navigation

- Start from the registered DeepSeek Moka URL.
- Navigate to the real jobs list and then job details as needed to collect stable hash-route job URLs.

### Success Signal

- A real jobs list shows individual job cards or rows.
- A reliable job entry has a title and a detail URL containing `#/job/<uuid>`.

## Apply

### Form Filling

- Use the visible `立即投递` / `申请职位` apply entry on the job detail page only after the role has been judged `recommended_apply`.
- Once the flow enters a DeepSeek/Moka `#/job/<id>/apply` URL, keep that same job exclusively active until exactly one terminal `update_jobs` state is written for the current item.
- Treat resume upload as an intermediate transition, never as success, failure, or permission to move to another job.

#### Pre-Upload Checkpoint

- On first entering `/apply`, read the current live form state before opening the resume upload chooser.
- Complete visible required fields and acknowledgements that are already present on the page when they can be answered from profile, CV, application facts, or this skill. Common DeepSeek/Moka pre-upload fields include name, phone, email, personal email, intended work city, consent/declaration checkbox, employment-type/full-time path, education, start-work date, and similar profile fields.
- The pre-upload checkpoint is not complete until each visible required field is either visibly committed or terminalized with a concrete blocker for the same job.
- Treat the DeepSeek/Moka intended-work-city control as commit-sensitive. Do not treat an option click alone as completion.
- Prefer one acceptable visible city first. For Hangzhou/Beijing roles, choose `杭州市` first when visible; choose `北京市` first only when the role/location is Beijing-only or Hangzhou is not available.
- Do not multi-select cities unless the page clearly shows multiple committed chips without leaving the control in an active search/dropdown state. If selecting a second city reopens or preserves a search/dropdown state, stop adding cities and commit the one already selected.
- After selecting a city option, close or blur the city control before judging it: use the safest visible action available, such as moving to the next required field, clicking a neutral form area, or using a browser-supported key action if available.
- Re-read the same `/apply` page after the city control is closed or blurred. The city is committed only when the observed form shows a selected city value/chip/text and the city row no longer presents a field-level required warning or active dropdown/search state.
- If the selected city text is visible but the city-specific required warning remains, make one alternative same-field commit attempt on the same `/apply` page: reopen or clear the control if possible, select only one acceptable city, close/blur it, and re-observe.
- If city commitment still cannot be verified after the bounded alternative attempt, write a same-job terminal `blocked` or `apply_failed` state with reason `pre_upload_city_not_committed`, including the current URL, selected visible city text if any, and unresolved validation evidence. Do not upload the resume for that item.
- For compliance, authenticity, privacy, code-of-conduct, rules acknowledgements, and factual declarations the applicant can truthfully make, select the affirmative/agree/confirm option when required.
- If an acknowledgement checkbox click or fill action fails or is uncertain, re-read the same `/apply` page, make at most one visible alternative attempt on that same control, then re-read again. If the control still cannot be verified as committed, write a same-job terminal `blocked` or `apply_failed` state with reason `pre_upload_acknowledgement_not_committed`.
- Do not open `上传简历` while a visible required pre-upload field, city control, acknowledgement, validation message, failed pre-upload tool action, or empty/sparse same-page observation remains unresolved.
#### Resume Upload And Post-Upload Closure

- After visible pre-upload requirements are handled and verified, upload the staged resume PDF through the visible resume/CV upload control.
- Immediately after `browser_file_upload`, keep the same DeepSeek/Moka `/apply` item active and classify the upload result for that exact job before any target switch.
- If `browser_file_upload` returns a useful non-empty page state with visible form fields, upload/file status, validation text, forward/review/submit controls, disabled-button reasons, or terminal text, continue the same job from that visible state.
- If `browser_file_upload` returns only the same `/apply` URL plus an empty, sparse, shell-only, or settling `### Snapshot`, treat that upload result as a same-job runtime/page blocker in the current DeepSeek runtime. Do not use stale pre-upload refs, do not keep probing with generic clicks, and do not start another job first.
- For that empty/sparse post-upload result, immediately write one terminal `update_jobs` row for the same current job using `application_status=blocked` or `apply_failed`, `apply_state=terminal_blocked` or `terminal_apply_failed`, `block_reason_type=runtime_or_page_issue`, `failure_pattern=post_upload_snapshot_empty_or_unstable`, the current `/apply` URL, and `last_apply_error` explaining that resume upload completed but the post-upload page could not be observed.
- If a fresh useful post-upload state is visible, choose one concrete branch:
  - Visible parsed profile fields, required questions, validation messages, city controls, acknowledgement controls, Continue/Next/Review/Submit/Confirm controls, or disabled-button reasons are present: handle the visible requirement from allowed context and continue the same job.
  - Upload is visibly pending, not accepted, rejected, or missing: retry or confirm upload once for the same job only if a visible upload control allows it, then re-read the same page.
  - Visible success, submitted, duplicate, already-applied, closed, withdrawn, or unavailable text appears: write the matching canonical terminal state immediately.
  - The `/apply` shell is visible but no usable forward control, required field, upload state, validation text, or terminal text can be observed: write a same-job terminal blocker `post_upload_apply_shell_without_progress_control` with current URL and visible page/control summary.
  - Browser/runtime/provider recovery prevents reliable post-upload observation after upload: write a same-job terminal `apply_failed` or `blocked` state with `block_reason_type=runtime_or_page_issue`, current URL, interrupted operation, and provider/runtime error text when available.
- Do not request unrelated context, navigate back to job lists, bootstrap another job, or switch apply targets after upload until this same job has one terminal `update_jobs` state.
- A provider/API overload or runtime interruption after a successful upload attempt is engineering evidence for the same current job. Record it as a same-job terminal runtime/page issue; do not treat it as a missing user fact or as permission to continue without terminalizing the current item.
#### Reusable Answers And Stop Rules

- Use factual profile context for name, email, phone, location, city, province, school, degree, work authorization, and similar profile fields.
- For gender questions, answer Male when required.
- For mixed full-time/internship employment-type questions, choose the full-time option when visible. For pure internship/campus availability, non-full-time availability, or ambiguous employment-type questions without a full-time option, stop the current job as `blocked` unless the page clearly supports full-time.
- For any required question whose answer is not available from profile/persona/CV/application facts/site skill, stop the current job as `blocked` and name the missing fact. Do not guess.
- Continue through safe next/review/submit steps until final submit only when all visible required fields are answered.
- The apply unit is complete only when the current job reaches one terminal `update_jobs` state: `submitted`, `already_applied`, `filtered_out`, `closed`, `blocked`, or `apply_failed`. Do not switch to another DeepSeek apply target first.
