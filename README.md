# CareerEng

`Python 3.11+` · `Local-first` · `Human-in-the-loop` · `Skill-driven browser automation`

A local AI workspace for job search, company research, application automation, and application tracking.

## Welcome to the New Era of AI

CareerEng is built on a broader premise: an effective job search is not only a matching problem, but also an iterative process of self-modeling, labor-market research, company understanding, and execution.

It is a local workspace for understanding target roles, studying target companies, and turning the job-search process into a repeatable AI-assisted system.

CareerEng is a local AI-assisted job-search, application, and application-tracking agent.

It turns a resume into a working job-search profile, helps discover target companies, registers company career sites, runs browser-based search/apply workflows through site-specific AI Skills, and tracks previously submitted applications so every run can report what changed.

```text
[Resume]
   -> [Persona]
   -> [Company Discovery]
   -> [Site Skills]
   -> [Browser Automation]
   -> [Reports]
```

## What It Does

| Area | Capability |
| --- | --- |
| Persona | Builds or updates `persona.md` from your resume and workspace context. |
| Company Discovery | Uses your resume, persona, intent, and job preferences to find target companies. |
| Site Registration | Lets you register companies manually or from LLM-generated company candidates. |
| Site Automation | Runs login, application-status review, job filtering, job retrieval, and apply workflows. |
| Skills | Keeps common rules in project-level Skills and website-specific behavior in site Skills. |
| Reports | Summarizes new jobs, submitted jobs, unsubmitted jobs, and reviewed application statuses. |

## Install

CareerEng requires Python 3.11+.

For normal use:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

For development and tests:

```bash
pip install -e ".[dev]"
python -m playwright install chromium
```

Resume PDF export uses Typst. Install it before using `careereng resume export-pdf`.

```bash
brew install typst
typst --version
```

## Initialize

Run onboarding once from the project root:

```bash
careereng onboard
```

This creates or reuses:

- `config.toml`
- `auth.json`
- `workspace/`

Add provider API keys to `auth.json`, then adjust `config.toml` as needed. For visible browser automation, keep:

```toml
[browser]
headless = false
browser_name = "chrome"
```

## Configuration

CareerEng uses two local configuration files:

- `auth.json` stores provider API keys.
- `config.toml` controls runtime behavior.

Do not commit real API keys. Keep `auth.json` local.

The main `config.toml` sections are:

- `[agent]`: controls non-browser LLM work, including persona generation, company discovery, routing, relatedness checks, and how many company candidates to return.
- `[browser]`: controls browser automation, including visible/headless mode, retry behavior, and site parallelism.
- `[browser.budgets]`: controls browser phase timeouts, step timeouts, max phase steps, and apply-job budgets.
- `[workspace]` or `[paths]`: controls where local workspace data is stored.
- `[providers.openai]` / `[providers.openrouter]`: controls OpenAI-compatible provider endpoints and structured-output behavior.

For site-skill development and debugging, prefer:

```toml
[browser]
headless = false
keep_open = true
site_parallelism = 1
```

For regular multi-site runs, `site_parallelism = 2` is a practical default. Increase it only after the active site skills are stable.

Browser automation uses `[providers.openai].api_base` and `[agent].default_model`; there is no separate browser `api_base` or browser model. If you use an OpenAI-compatible proxy or gateway, update `[providers.openai].api_base`, then put the matching key in `auth.json`.

Common browser budget knobs live under `[browser.budgets]`, for example:

```toml
[browser.budgets]
session_preparation_phase_timeout_seconds = 420
application_status_review_phase_timeout_seconds = 300
apply_job_phase_timeout_seconds = 3600
apply_job_timeout_ms = 180000
```

## Resume Contract

CareerEng expects one current resume source and one apply-ready PDF.

- Current resume source: `workspace/cv/current/`
- Resume history: `workspace/cv/history/`
- Apply-ready PDF: `workspace/cv/exports/<one-file>.pdf`

Do not keep multiple PDFs in `workspace/cv/exports/`. The apply flow treats multiple PDFs as ambiguous.

Recommended flow:

```bash
careereng resume upload --file ./resume.md
careereng resume export-pdf --file ./resume.md --output resume.cv.pdf
careereng profile generate
```

`resume upload` syncs the resume into the workspace and updates profile context. `resume export-pdf` converts Markdown to PDF through Typst, archives changed Markdown into history, and keeps `workspace/cv/exports/` focused on the current apply PDF.

## Quick Start

The normal flow is: initialize, add resume, discover companies, register companies, then run retrieval/apply.

```bash
careereng onboard

# 1. Add resume and generate persona
careereng resume upload --file ./resume.md
careereng resume export-pdf --file ./resume.md --output resume.cv.pdf
careereng profile generate

# 2. Ask for target companies
careereng run -m "Find the top 10 foreign technology companies that fit my software engineering background"

# 3. Register companies from the returned list by replying with indices
careereng run -m "1 3 5"

# 4. Run retrieval and apply for active registered sites
careereng jobs apply

# 5. Generate or inspect the latest job report
careereng report jobs --batch latest
```

You can also register a company directly:

```bash
careereng site add "Microsoft" --url https://careers.microsoft.com
```

## Skills

CareerEng uses AI Skills as procedural instructions for the agent. Skills are plain Markdown files with YAML front matter.

The important layers are:

- Project job skill: `skills/search/jobs/SKILL.md`
- User job preference skill: `workspace/skills/jobs/SKILL.md`
- Site skill: `skills/search/jobs/sites/<site>/SKILL.md` or `workspace/sites/<site>/skills/SKILL.md`

Priority is site-specific first during a site workflow, then user preferences, then project defaults, then `intent.md`.

Project-level job skill defines common behavior:

- search targets
- filtering rules
- matching rules
- application-status review policy
- auth recovery signal
- report-relevant recording rules

Site skills define website-specific behavior:

- how to log in
- how to review submitted applications
- how to filter jobs
- how to detect already-applied jobs
- how to fill site-specific forms
- what counts as a successful submission

When adding a new site, use AI to inspect the website and draft the site skill. Keep browser decisions in the skill, not hard-coded in Python.

## Site Workflow

The normal registered-site workflow is:

```text
session_preparation
-> application_status_review
-> channel_discovery
-> job_filtering
-> job_retrieval
-> apply
```

`session_preparation` must finish login before later phases run. If a later phase discovers that the session expired or the site requires login again, the workflow returns to `session_preparation` and then resumes the interrupted phase.

Human-only steps are intentionally blocked for user takeover:

- password entry
- MFA
- verification code
- CAPTCHA
- email confirmation
- ambiguous required answers that cannot be derived from persona/CV/skill context

## Reports

Each job batch writes run state and reports under `workspace/`.

Important outputs:

- Batch state: `workspace/jobs/batches/<batch_id>.json`
- Per-site run jobs: `workspace/sites/<site>/jobs/runs/<batch_id>.jsonl`
- Per-site job history: `workspace/sites/<site>/jobs/history_jobs.json`
- Daily reports: `workspace/reports/jobs/YYYY-MM-DD/`
- Daily final report: `workspace/reports/jobs/YYYY-MM-DD/final.md`

Reports summarize:

- retrieved jobs
- new jobs not already in local history
- new submitted jobs
- new unsubmitted or filtered jobs
- application-status review results grouped by status
- site-level retrieval/apply outcomes

## Common CLI

| Command | Purpose |
| --- | --- |
| `careereng onboard` | Create local config and workspace scaffolding. |
| `careereng run -m "..."` | Send a normal chat/search/site-management instruction. |
| `careereng resume upload --file ./resume.md` | Import resume text into the workspace. |
| `careereng resume export-pdf --file ./resume.md --output resume.cv.pdf` | Convert Markdown resume to the apply-ready PDF. |
| `careereng profile generate` | Generate or update `persona.md`. |
| `careereng site add "Microsoft" --url https://careers.microsoft.com` | Register a company career site directly. |
| `careereng site list --status active` | List active registered sites. |
| `careereng site deactivate microsoft` | Disable a site without deleting local history. |
| `careereng site activate microsoft` | Reactivate a registered site. |
| `careereng jobs apply` | Run retrieval and apply for active registered sites. |
| `careereng report jobs --batch latest` | Generate or inspect the latest job report. |
| `careereng batch-list` | List open job batches. |
| `careereng batch-clear` | Mark stale open batches as cancelled. |

Debug commands:

```bash
careereng batch-debug-create --site amd --batch latest --title "Software Engineer"
careereng batch-apply --site amd --batch <debug_batch_id> --limit 1
```

## Current Site Skills

| Site | Skill Status | Apply |
| --- | --- | --- |
| AMD | Ready | Enabled |
| Microsoft | Ready | Enabled |
| NVIDIA | Ready | Enabled |
| Qualcomm | Ready | Enabled |
| Amazon AWS | Draft / example | Disabled |
| SAP | Draft / example | Disabled |

## Safety Notes

CareerEng is designed as a human-in-the-loop local assistant, not a blind auto-submit bot.

- Keep `browser.headless = false` while developing or debugging site skills.
- Review generated site skills before enabling `apply_enabled: true`.
- Do not store multiple apply PDFs in `workspace/cv/exports/`.
- Use `careereng batch-clear` to mark stale open batches as cancelled; it does not kill OS browser processes.
- If a site gets stuck after network instability or page reloads, stop the run and restart from the CLI rather than editing browser state manually.

## Tips

CareerEng is intentionally AI-friendly.

Most behavior is expressed through Markdown Skills, JSONL state, and small Python orchestration modules. If you want to add a new site skill, tune an existing workflow, or change what reports show, it is usually practical to ask an LLM to inspect the current files and make the change with you.

For example, the report layer can be extended to answer questions such as:

- How long does a specific company usually take to respond?
- How many days pass between first seeing a job, applying, and observing a status change?
- Which companies reject quickly, keep applications active, or leave them unresolved?

The raw ingredients for this kind of analysis are already recorded across job runs, history files, application reviews, and daily reports. The recommended workflow is to describe the desired report insight in natural language, let an LLM locate the relevant storage files and report code, and then make a small targeted change.
