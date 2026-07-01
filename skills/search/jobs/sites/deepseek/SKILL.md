---
id: site-deepseek
name: deepseek Site Skill
version: v1
updated_at: '2026-06-27'
scope: site
site_key: deepseek
status: ready
apply_enabled: true
job_identity:
  fragment_job_route_patterns:
  - '#/job/{site_job_id}'
  - '#/jobs/{site_job_id}'
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

- No reliable DeepSeek application-review workflow has been confirmed yet.
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
- On first entering `/apply`, read the current live form state before opening the resume upload chooser.
- Before resume upload, complete visible required fields and acknowledgements that are already present on the page when they can be answered from profile, CV, application facts, or this skill. Common DeepSeek/Moka pre-upload fields include name, phone, email, personal email, intended work city, consent/declaration checkbox, employment-type/full-time path, education, start-work date, and similar profile fields.
- For intended work city controls, follow the project-wide city-selection rule: select all acceptable visible cities if multi-select is supported; if the control is visibly single-select, choose an acceptable visible China target city and record the choice in phase memory.
- Do not treat a city option click as complete until a fresh same-page observation shows a committed selected value/chip, no field-level required warning, or a clear value in the field.
- For compliance, authenticity, privacy, code-of-conduct, rules acknowledgements, and factual declarations the applicant can truthfully make, select the affirmative/agree/confirm option when required.
- Do not open `上传简历` while a visible required pre-upload field, city control, acknowledgement, validation message, or failed pre-upload tool action on the same page is unresolved.
- If a city/location option click or acknowledgement checkbox click fails or is uncertain, re-read the same `/apply` page, make at most one visible alternative attempt on that same field/control, then re-read again. If the control still cannot be verified as committed, write a same-job terminal `blocked` or `apply_failed` state with a specific reason such as `pre_upload_city_not_committed` or `pre_upload_acknowledgement_not_committed`.
- After visible pre-upload requirements are handled, upload the staged resume PDF through the visible resume/CV upload control.
- Immediately after `browser_file_upload`, stay on the same `/apply` URL and use a fresh `browser_snapshot`-style same-page observation as the first post-upload read. Do not use stale pre-upload refs and do not switch targets.
- Do not make `browser_evaluate` the first post-upload observation on DeepSeek/Moka. Use it only after a normal snapshot shows enough page structure to justify targeted inspection.
- If the first post-upload observation is sparse, shell-only, still settling, or only shows an empty `### Snapshot`, wait briefly and re-read the same `/apply` page once with another same-page snapshot before classification.
- From the freshest post-upload page state, choose one concrete branch:
  - Visible parsed profile fields, required questions, validation messages, city controls, acknowledgement controls, Continue/Next/Review/Submit/Confirm controls, or disabled-button reasons are present: handle the visible requirement from allowed context and continue the same job.
  - Upload is still processing, visibly pending, not accepted, rejected, or missing: retry or confirm upload once for the same job if a visible upload control allows it, then re-read the same page.
  - Visible success, submitted, duplicate, already-applied, closed, withdrawn, or unavailable text appears: write the matching canonical terminal state immediately.
  - The `/apply` shell is visible after the allowed re-observation but no usable forward control, required field, upload state, validation text, or terminal text can be observed: write a same-job terminal blocker `post_upload_apply_shell_without_progress_control` with current URL and visible page/control summary.
  - Both post-upload same-page observations are empty/sparse and no reliable form state can be obtained: write a same-job terminal `apply_failed` or `blocked` state with `runtime_or_page_issue` and reason `post_upload_snapshot_empty_or_unstable`, including the current URL and the last confirmed operation `browser_file_upload`.
  - Browser/runtime recovery prevents reliable post-upload observation after the same-page reread attempt: write a same-job terminal `apply_failed` or `blocked` state with `runtime_or_page_issue`, current URL, and the interrupted operation.
- Use factual profile context for name, email, phone, location, city, province, school, degree, work authorization, and similar profile fields.
- For gender questions, answer Male when required.
- For mixed full-time/internship employment-type questions, choose the full-time option when visible. For pure internship/campus availability, non-full-time availability, or ambiguous employment-type questions without a full-time option, stop the current job as `blocked` unless the page clearly supports full-time.
- For any required question whose answer is not available from profile/persona/CV/application facts/site skill, stop the current job as `blocked` and name the missing fact. Do not guess.
- Continue through safe next/review/submit steps until final submit only when all visible required fields are answered.
- The apply unit is complete only when the current job reaches one terminal `update_jobs` state: `submitted`, `already_applied`, `filtered_out`, `closed`, `blocked`, or `apply_failed`. Do not switch to another DeepSeek apply target first.
