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

### Matching Override

- Use the project common matching rule for DeepSeek roles.
- Prioritize AI/core engineering and model-data strategy roles that match the current persona/CV, especially `Agent`, `深度学习`, `搜索算法`, `预训练`, `核心系统`, `AI超算`, `Harness`, `全栈`, `AI核心研发`, `模型数据策略`, `数据策略`, `模型数据`, `AI跨界人才`, backend, systems, infrastructure, and data/agent engineering roles.
- Do not apply to pure internship/campus/student/new-grad/co-op/校招/应届 roles.
- For mixed `全职/实习` or `实习/全职` roles, do not filter at list stage only because of the mixed label. If the role is a target AI/core role, proceed with normal JD/CV matching and choose the full-time path when the form asks. If the live path only supports internship, record the job as blocked or filtered with that concrete reason.
- If the role is a product, operations, data analyst, non-engineering, or hardware/facilities role and the JD does not strongly connect to software/AI agent engineering, mark it `filtered_out`.
- If the current local history already shows a terminal submitted/already-applied/rejected/closed/withdrawn state for the same hash-route job id, do not submit again.

### Form Filling

- Use the visible `立即投递` / apply entry on the job detail page only after the role has been judged `recommended_apply`.
- Upload the staged resume PDF when the form asks for a resume/CV.
- Uploading the resume is not a terminal outcome. After selecting/uploading the staged PDF, wait for the upload state to settle, re-read or resnapshot the live page, then continue with the next visible form step, validation, review, or final submit action.
- For DeepSeek/Moka apply, after `browser_file_upload` succeeds, immediately take a fresh live page snapshot before any navigation to another job. If the snapshot is empty or only shows shell content, wait briefly and snapshot again. Stay on the same apply URL until the page either exposes parsed profile fields, required questions, a review/submit step, a clear human-only blocker, or a concrete site/runtime failure.
- Do not leave a DeepSeek apply URL after upload unless exactly one terminal `update_jobs` state has been written for the current job: `submitted`, `already_applied`, `filtered_out`, `closed`, `blocked`, or `apply_failed`.
- If the upload parsed the resume and required fields become visible, fill them from profile/CV/apply facts and continue to final submit when safe; do not treat upload completion as success, failure, or a reason to advance to the next apply target.
- If the page stays on the same upload step after the file chooser completes, inspect visible upload status, required-field validation, disabled buttons, and next-step controls before retrying upload. Do not move to another job without writing a terminal `update_jobs` state or a structured loop-control gap.
- Use factual profile context for name, email, phone, location, city, province, school, degree, work authorization, and similar profile fields.
- For gender questions, answer Male when required.
- For compliance, code-of-conduct, authenticity, privacy, and rules acknowledgements, select Yes when the statement is an agreement/confirmation the applicant can truthfully make.
- For mixed full-time/internship employment-type questions, choose the full-time option when visible. For pure internship/campus availability, non-full-time availability, or ambiguous employment-type questions without a full-time option, stop and ask the user unless the page clearly supports full-time.
- For any required question whose answer is not available from profile/persona/CV/site skill, stop the current job as `blocked` and ask the user to take over or provide the answer. Do not guess.
- Continue through safe next/submit steps until the final submit action only when all required visible fields are answered.

### Site Signals

- Treat visible success text after final submission, such as submitted successfully / 投递成功 / 申请已提交 / 简历投递成功, as `application_status=submitted`, `apply_state=terminal_submitted`, and `decision_status=recommended_apply`.
- Treat visible already-applied text, duplicate application text, or a job detail/application page that clearly says the current user already submitted as `application_status=already_applied`.
- Treat a missing/closed job page, no-position page, or unavailable job detail as `application_status=closed`.

### Escalation

- Stop and ask the user to take over for password entry, verification, CAPTCHA, phone/email code, ambiguous required personal answers, or any page where the visible form cannot be safely completed from local profile/persona/CV facts.
