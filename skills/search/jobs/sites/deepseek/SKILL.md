---
id: site-deepseek
name: deepseek Site Skill
version: v1
updated_at: '2026-06-23'
scope: site
site_key: deepseek
status: ready
apply_enabled: false
job_identity:
  fragment_job_route_patterns:
  - '#/job/{site_job_id}'
  - '#/jobs/{site_job_id}'
---
# DeepSeek Site Skill

DeepSeek currently uses a Moka/High-Flyer recruiting surface.

## Site Policy

### Retrieval Policy

- In `job_filtering`, use the visible `职能类型` filter option `全栈开发和算法` as the primary engineering filter when available.
- Treat AI/core engineering keywords as high-priority DeepSeek retrieval targets: `Agent`, `深度学习`, `搜索算法`, `预训练`, `核心系统`, `AI超算`, `Harness`, `全栈`.
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
- Hard-exclude intern, campus, student, new-grad, co-op, 校招, and 实习 roles.

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

- No DeepSeek-specific matching override has been confirmed.

### Form Filling

- Apply is disabled until DeepSeek-specific apply behavior is tested and approved.

### Site Signals

- No DeepSeek-specific submitted/already-applied signals have been confirmed.

### Escalation

- Stop and ask the user to take over for password entry, verification, CAPTCHA, or ambiguous required personal answers.
