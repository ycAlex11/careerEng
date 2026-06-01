# CareerEng

`Python 3.11+` · `Local-first` · `Human-in-the-loop` · `AI Skills` · `Browser automation` · `Codex-ready`

A local AI workspace for running an adaptive, evidence-driven job search across company career sites.

## What CareerEng Is

CareerEng is not a closed job-search app. It is a local, AI-operable workspace for running an adaptive job search with Codex or other AI assistants.

Humans decide goals, safety boundaries, target companies, and whether to submit applications. Codex/LLMs inspect local files, run commands, draft Skills, explain reports, and propose improvements. CareerEng stores the durable state: resume, persona, intent, site Skills, job history, reports, metrics, memory, action cards, and evolution evidence.

When action is needed, browser automation becomes the execution layer: opening company career sites, reviewing application status, retrieving matching jobs, and applying when the active Skills and local context say it is appropriate.

```text
[Resume + Preferences]
   -> [Persona + Intent]
   -> [Company Discovery]
   -> [Project + Site Skills]
   -> [Assistant Bridge + Browser Execution]
   -> [Application History + Reports + Metrics]
   -> [Evidence Packs + Better Next Runs]
```

## What It Does

| Area | Capability |
| --- | --- |
| Persona | Builds or updates `persona.md` from your resume and workspace context. |
| Company Discovery | Uses your resume, persona, intent, and job preferences to find target companies. |
| Site Registration | Lets you register companies manually or from LLM-generated company candidates. |
| Site Automation | Runs login, application-status review, job filtering, job retrieval, and apply workflows. |
| Skills | Keeps shared job-search policy in project Skills and website-specific behavior in site Skills. |
| Reports | Summarizes new jobs, submitted jobs, reviewed application statuses, raw status labels, and status changes. |
| Application Summary | Builds a local summary of application outcomes, unmatched reviews, and repair opportunities. |
| Assistant Bridge | Lets Codex or other assistants route `@career` requests into local commands and career memory. |
| Action Cards | Creates local review task cards when Codex/user judgment is needed instead of immediate execution. |
| Metrics | Records runtime and usage summaries for debugging and future workflow optimization. |
| Evolution Scaffolding | Builds evidence packs, proposals, evaluations, and rollback records for future workflow improvement. |

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

## AI Assistant Usage

CareerEng is designed to be operated with Codex or another AI assistant. The main entry point is simple: start a message with `@career`.

Use `@career` when you want the assistant to operate CareerEng instead of only chatting about the project. The assistant sends the message into CareerEng, CareerEng classifies it, records it, and returns the suggested local action.

Examples:

```text
@career 查看一下投递情况
@career 检查投递状态
@career 总结一下我们的投递情况
@career 激活高通和 AMD
@career 停用英伟达
@career 我想投 AI infra，需要补什么？
```

Codex should route these messages through the local assistant bridge. Detailed assistant rules live in `AGENTS.md` and `docs/assistant_bridge/`.

The important design boundary is simple: Codex can understand the current conversation and help draft changes; CareerEng owns local storage, command execution, history, reports, and business state.

## Skills

CareerEng uses AI Skills as procedural memory for the agent. Skills are plain Markdown files with YAML front matter.

The active runtime layers are:

- Project job skill: `skills/search/jobs/SKILL.md`
- Site skill: `skills/search/jobs/sites/<site>/SKILL.md`

Workspace-level Skill files may exist under `workspace/`, but they are not part of the active runtime contract. Treat them as legacy or local notes unless a specific workflow explicitly loads them.

During a site workflow, site-specific instructions take priority over project defaults. User preferences come from the current request, `persona.md`, `intent.md`, resume context, and local history.

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

When adding a new site, use AI to inspect the website and draft the site skill. Keep browser decisions in Skills; Python should stay focused on orchestration, browser sessions, timeouts, persistence, and safety gates.

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
- raw status labels observed on career sites
- status changes since the previous known application state
- site-level retrieval/apply outcomes
- unmatched application review records that need history repair or future enrichment

Application summaries are separate from human-facing reports. They are built from local history and review data, and are intended to support later analysis and evolution:

```bash
careereng application-summary build
careereng application-summary repair-history
```

Use summaries when you want to answer questions such as:

- How many applications are still active?
- Which applications were rejected, closed, withdrawn, or forwarded?
- Which review records came from the site dashboard but were not yet matched to local job history?
- Which history rows can be repaired safely from stronger site/job identifiers?

## Metrics And Evolution

CareerEng records runtime evidence so future changes can be evaluated instead of guessed.

Metrics are stored under `workspace/metrics/` and can be summarized with:

```bash
careereng metrics summary
careereng metrics summary --batch latest
careereng metrics summary --site nvidia
careereng metrics summary --phase job_retrieval
```

The metrics layer records request timing, stream event types, tool-call counts, and token usage when the provider returns usage data. This is useful for debugging slow phases, no-progress loops, unstable site skills, and future workflow optimization.

Evolution is evidence-backed, not blind self-modification. CareerEng records what happened, builds an evidence pack, lets Codex/LLMs propose targeted changes, evaluates later outcomes, and keeps rollback records.

What can evolve today:

- Site workflow behavior: improve how a site Skill logs in, reviews applications, searches, retrieves jobs, detects already-applied jobs, fills forms, or avoids no-progress loops.
- Application matching strategy: improve how the system decides whether a job should be applied to, using persona/CV, JD text, rejection patterns, in-process signals, and company-specific evidence.
- Assistant routing and memory intake: improve when `@career` or a Codex conversation should enter local CareerEng memory, and how it should be classified.
- Resume/profile direction: use accumulated job-search outcomes, application feedback, and user-confirmed facts to suggest resume/persona/profile changes.
- History repair and data quality: reduce unmatched application reviews, enrich missing job IDs/JD fields, and make local history more useful for future reasoning.

The evolution cadence is intentionally configurable. Some users may ask Codex to review a site Skill after every 10 successful runs; others may wait for repeated failures, repeated fast rejections, repeated unmatched records, or enough before/after metrics to compare behavior.

Detailed evolution rules, candidate specs, proposal schema, evaluation, and rollback behavior live in `docs/evolution/`.

## Local Debugging And Evolution Data

CareerEng records structured local data so an assistant can debug runs from evidence instead of guessing.

Important local signals include:

- Assistant bridge events: `workspace/assistant_bridge/`
- Action cards for Codex/user follow-up: `workspace/action_cards/`
- Career memory signals: `workspace/memory/`
- Metrics summaries and usage records: `workspace/metrics/`
- Browser-control evolution events: `workspace/evolution/browser_control/`

These files are intentionally local-first. They can support later improvements such as better command routing, more accurate application summaries, retrieval-stop tuning, site-skill refinement, and stronger application matching.

## Common CLI

| Command | Purpose |
| --- | --- |
| `careereng onboard` | Create local config and workspace scaffolding. |
| `careereng run -m "..."` | Send a normal chat/search/site-management instruction. |
| `careereng resume upload --file ./resume.md` | Import resume text into the workspace. |
| `careereng resume export-pdf --file ./resume.md --output resume.cv.pdf` | Convert Markdown resume to the apply-ready PDF. |
| `careereng profile generate` | Generate or update `persona.md`. |
| `careereng site add "Microsoft" --url https://careers.microsoft.com` | Register a company career site directly. |
| `careereng site bootstrap "Apple" --url https://jobs.apple.com` | Prepare a draft site AI Skill action card and evidence pack for a new site without running browser phases. |
| `careereng site list --status active` | List active registered sites. |
| `careereng site deactivate microsoft` | Disable a site without deleting local history. |
| `careereng site activate microsoft` | Reactivate a registered site. |
| `careereng jobs apply` | Run retrieval and apply for active registered sites. |
| `careereng report jobs --batch latest` | Generate or inspect the latest job report. |
| `careereng jobs review-status` | Review application status for active registered sites and stop after reporting. |
| `careereng application-summary build` | Build an application summary from local history and review data. |
| `careereng application-summary repair-history` | Apply safe history repairs for legacy unmatched review records. |
| `careereng metrics summary` | Summarize runtime and usage metrics. |
| `careereng evolution candidates` | List available evolution candidate specs. |
| `careereng evolution run --candidate <candidate_id>` | Archive an evidence pack for one evolution candidate. |
| `careereng evolution evaluate --run <run_id>` | Evaluate an applied evolution run and write selection results. |
| `careereng evolution rollback --run <run_id>` | Roll back an applied evolution run from archived snapshots. |
| `careereng evolution trigger-scan` | Scan local evidence and create evolution triggers. |
| `careereng assistant ingest --client codex --thread <id> -m "@career ..."` | Route an external assistant message through the local assistant bridge. |
| `careereng assistant state --client codex --thread <id>` | Inspect assistant bridge thread scope. |
| `careereng assistant end --client codex --thread <id>` | Close assistant bridge career scope for a thread. |
| `careereng action-card list` | List open Codex/user review task cards. |
| `careereng action-card show <card_id>` | Show one action card as Markdown. |
| `careereng action-card close <card_id> --result "..."` | Mark an action card as done. |
| `careereng career-memory promote` | Promote assistant bridge signals into unified career memory units. |
| `careereng career-memory import-candidates <file>` | Import Codex-curated memory candidates from JSON or JSONL. |
| `careereng career-memory list` | Inspect stored career memory units. |
| `careereng batch-list` | List open job batches. |
| `careereng batch-clear` | Mark stale open batches as cancelled. |
| `careereng batch-stop` | Stop CareerEng manager/browser runtime processes owned by the current workspace. |

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

Use AI to operate CareerEng.

CareerEng is intentionally structured to be friendly to assistants like Codex: commands are exposed through the CLI, workflows are described in Markdown Skills, durable state is stored locally, and reports, metrics, memory, action cards, and evidence packs are readable by both humans and AI.

When you want to add a site, tune a skill, understand a failed run, change a report, repair history, summarize outcomes, or evolve a workflow, the recommended path is to ask Codex or another LLM to inspect the local files and make a targeted change with you.

Evolution should also be adjusted this way. The default cadence and trigger rules are only starting points; you can ask AI to tune the evolution rhythm based on your own situation, data volume, risk tolerance, and job-search strategy. For example, one user may want to review a site Skill after every 10 runs, while another may wait for stronger evidence such as repeated failures, repeated rejections in the same role family, or enough metrics to compare before/after behavior.
