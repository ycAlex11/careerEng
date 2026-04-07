# CareerEng (V1.1)

CareerEng is a lightweight CLI assistant for automated job search and application workflows.

## Scope

- Single chat entry: `careereng run -m "..."`
- Resume upload: `careereng resume upload --file <path>`
- Report review flow:
  - `careereng report list`
  - `careereng report review --id <report_id>`
- Provider support: OpenAI/OpenRouter (`config.toml` + `auth.json`)
- Session isolation (`workspace/sessions/<session_id>.jsonl`)
- Relatedness-first context routing for chat turns
- Candidate events + auto report (every 20 related events)
- Resume sync flow:
  - Apply `persona.md` patch automatically
  - Generate `intent.md` candidate patch and require `y/n` confirmation
- V1.1 search/apply flow:
  - Extract constraints from `message + intent + project search skills + workspace job skill`
  - LLM recommends company candidates (Top-N)
  - User selects companies by index (`1 3 5`)
  - Google/Playwright resolves each selected company's entry URL
  - Registration stage writes the site registry + site metadata + site runtime scaffold
  - Registered-site retrieve/apply batch runs through `run -m`, with blocked-login recovery by `site_key y/n`
- Site registry commands: `careereng site add/list/activate/deactivate`

## Install

```bash
pip install -e .
pip install playwright
python -m playwright install chromium
careereng onboard
```

`careereng onboard` ensures the editable runtime scaffold exists before first use, including:

- `config.toml`
- `auth.json`
- `workspace/...`

## Configuration

After `careereng onboard`, fill in and adjust:

- `config.toml`
- `auth.json`

Set provider config in `config.toml`:

```toml
[workspace]
path = "./workspace"

[agent]
default_provider = "openai"
default_model = "gpt-4o-mini"
max_history_messages = 50
related_history_k = 6
relatedness_threshold = 0.7
site_parallelism = 2
router_confidence_threshold = 0.75
router_log_enabled = true
search_company_top_k = 10

[browser]
headless = false
timeout_ms = 45000
slow_mo_ms = 0

[providers.openrouter]
api_base = "https://openrouter.ai/api/v1"
structured_output_mode = "auto"

[providers.openai]
api_base = "https://api.openai.com/v1"
structured_output_mode = "auto"
```

Browser notes:

- `headless = false` means Playwright will open a visible Chromium window during search/apply flows.
- If you prefer hidden browser automation, set `headless = true`.

`structured_output_mode` controls how provider-side JSON output is requested.
Recommended default is `auto`, which tries `json_schema`, then `json_object`, then falls back to text-repair.

Set provider keys in `auth.json`:

```json
{
  "providers": {
    "openrouter": {
      "api_key": ""
    },
    "openai": {
      "api_key": "sk-..."
    }
  }
}
```

## Commands

```bash
careereng onboard
careereng run -m "我想找中国后端岗位" --session cli:default
careereng resume upload --file ./cv.md --session cli:default
careereng report list
careereng report review --id profile_report_xxx
careereng route feedback --event-id route_evt_xxx --correct no --expected-route search --comment "should be search"
careereng site add "Microsoft"
careereng site list --status active
careereng site deactivate microsoft
careereng site activate microsoft --url https://careers.microsoft.com
careereng run -m "开始检索并投递已注册的公司"
careereng run -m "microsoft y"
```

Search conversation example (all through `run -m`):

```bash
careereng run -m "请帮我搜索后端岗位，偏外企"
# assistant returns company list -> reply: 1 3 5
# assistant registers those companies, resolves entry URLs, and creates site skill templates

careereng run -m "开始检索并投递已注册的公司"
# ready sites continue automatically
# blocked sites ask for manual login recovery
# reply: microsoft y
```

## Repository Layout

```text
careerEng/
├─ skills/
│  ├─ README.md
│  ├─ resume-sync/SKILL.md
│  └─ search/
│     ├─ SKILL.md
│     ├─ jobs/SKILL.md
│     └─ people/SKILL.md
├─ evals/
│  ├─ relatedness/
│  │  ├─ few_shot.yaml
│  │  ├─ evaluator.yaml
│  │  └─ dataset.jsonl
│  ├─ router/dataset.jsonl
│  ├─ profile_extractor/few_shot.yaml
│  └─ intent_extractor/few_shot.yaml
├─ careereng/
│  ├─ cli/
│  ├─ agent/
│  ├─ storage/
│  ├─ tools/
│  └─ providers/
└─ workspace/
```

## Skill Layers

Search skill split:

- `skills/search/SKILL.md`: project-level search orchestration policy
- `skills/search/jobs/SKILL.md`: project-level job/company retrieval method
- `workspace/skills/jobs/SKILL.md`: user-level job preference overlay scaffolded by `careereng onboard`; higher priority than `intent.md` during company recommendation

## Workspace Layout

Run `careereng onboard` once to scaffold the editable workspace files before your first real session.

```text
workspace/
├─ sessions/
├─ sessions_state/
├─ chat/
│  ├─ all.jsonl
│  └─ daily/YYYY-MM-DD.jsonl
├─ profile/
│  ├─ persona.md
│  ├─ history/
│  ├─ profile_events.jsonl
│  ├─ reports/
│  └─ sources/
├─ intent/
│  ├─ intent.md
│  ├─ history/
│  ├─ intent_events.jsonl
│  └─ reports/
├─ skills/
│  └─ jobs/SKILL.md    # user-owned preference overlay, created by `careereng onboard`
├─ search/
│  ├─ queries.jsonl
│  ├─ web_results.jsonl
│  ├─ company_candidates.jsonl
│  └─ company_decisions.jsonl
├─ applications/
│  ├─ all.jsonl
│  └─ events.jsonl
├─ jobs/
│  ├─ batches/<batch_id>.json
│  └─ events.jsonl
├─ router/
│  ├─ events.jsonl
│  └─ feedback.jsonl
├─ sites/
│  ├─ registry.jsonl
│  └─ <site_id>/
│     ├─ site.json
│     ├─ browser/
│     │  ├─ session.json
│     │  └─ user_data/
│     ├─ jobs/runs/<batch_id>.jsonl
│     ├─ jobs/history_jobs.json
│     ├─ jobs/descriptions/<hash>.md
│     ├─ jobs/features.jsonl
│     ├─ applications/YYYY-MM-DD.jsonl
│     ├─ events/all.jsonl
│     └─ skills/SKILL.md
└─ runs/daily/YYYY-MM-DD.jsonl
```

## Default Documents

`profile/persona.md` defaults include:

- `basic.nationality: China`
- `basic.current_city: Taiyuan`
- `constraints.visa: none`
- `constraints.work_auth: china`

`intent/intent.md` defaults include:

- `target_locations: ["China"]`
- `location_note: "Any city in China is acceptable"`
- `company_preferences: []`
- `date_posted_after: <today-30d>`

## Resume Sync (V1.1)

- `resume upload` is an explicit profile/intention sync flow.
- Resume parsing follows `skills/resume-sync/SKILL.md`.
- Resume parsing does not inject `evals/*/few_shot.yaml` examples.
- If resume extraction output is non-JSON, system runs one JSON patch repair retry.
- If persona updates but intent extraction is empty, system generates a fallback intent candidate for `y/n` confirmation.
- The system extracts and writes a persona patch automatically.
- The system infers a conservative intent patch candidate.
- User confirms `y/n` before writing intent patch.

## Relatedness + Reports

- Chat turns are first evaluated for profile/intent relatedness.
- Only messages above threshold are counted as related.
- Every 20 related events (profile and intent counted separately) triggers a new report.
- In review, user marks each item relevant/irrelevant; only relevant items become patch candidates.
- Final `y/n` decides whether patch is applied to `persona.md` / `intent.md`.

## Site Workflow (V1)

When user selects companies for registration:

- Resolve an entry URL (official careers first, Google fallback)
- Register/update `workspace/sites/registry.jsonl`
- Create or reuse `workspace/sites/<site_id>/site.json`
- Create or reuse `workspace/sites/<site_id>/skills/SKILL.md` with YAML front matter:
  - `status: draft|ready`
  - `apply_enabled: true|false`
- Create or reuse `workspace/sites/<site_id>/browser/session.json`
- Create or reuse `workspace/sites/<site_id>/browser/user_data/`
- Do not write `jobs/catalog.jsonl` or `jobs/discoveries/*` during registration

## Registered-Site Retrieve/Apply (V1.1)

- Batch trigger stays in chat: `careereng run -m "开始检索并投递已注册的公司"`
- The system only auto-applies when the site skill is both:
  - `status: ready`
  - `apply_enabled: true`
- Preflight checks before a site can apply:
  - site is `active`
  - `entry_url` exists
  - site skill exists and is `ready`
  - browser session is ready
- If `apply_enabled: false`, the site still runs retrieval only.
- If session is not ready, the site becomes `blocked_login` and the batch keeps going for other ready sites.
- Recovery flow:
  - reply `site_key y` to continue that site
  - if login browser is opened, finish login, close the site window, then reply `site_key y` again
  - reply `site_key n` to skip that blocked site
- First version keeps Chromium visible by default (`[browser].headless = false`) for easier debugging.

## V1.1 Search + Storage Strategy

- Conflict priority: `current message > search skills > intent.md > defaults`
- Routing strategy:
  - LLM route decision first (`chat/search/site/jobs_batch` + confidence + params)
  - High confidence executes directly; medium confidence asks `y/n`; low confidence falls back to normal chat or deterministic detector
  - Every route decision is logged in `workspace/router/events.jsonl`
  - User confirmation / rejection is logged in `workspace/router/feedback.jsonl`
- Search pipeline:
  - `Extract`: build structured query spec
  - `Reason`: LLM generates company shortlist from persona + intent + jobs skill
  - `Select`: user selects company indices for registration
  - `Locate`: optional Playwright Google lookup for apply channels (careers/workday/greenhouse/lever)
  - `Calibrate`: low confidence jobs are not auto-applied
- Search flow modules:
  - `careereng/agent/search_flow.py`: selection parsing + registration summary
  - `careereng/agent/channel_locator.py`: apply channel lookup/scoring + official careers优先停止
- Registration storage policy:
  - Source of truth: `workspace/sites/registry.jsonl`
  - Per-site metadata: `workspace/sites/<site_id>/site.json`
  - Legacy `catalog.jsonl` is seeded into `jobs/history_jobs.json` when history is still empty
  - Legacy discoveries are marked with `site.json.legacy_discoveries_dirty = true`
- Job storage policy:
  - Registration does not write site job results
  - Each successful retrieval run writes per-site run data to `workspace/sites/<site_id>/jobs/runs/<batch_id>.jsonl`
  - After `job_retrieval` finishes successfully, that run is merged into `workspace/sites/<site_id>/jobs/history_jobs.json`
  - Retrieve/apply batch state lives in `workspace/jobs/batches/<batch_id>.json`
  - Batch event history lives in `workspace/jobs/events.jsonl`
  - If retrieval succeeds but apply fails, discovered jobs are kept and the batch/site result is marked as "retrieved but not applied"

## Skill Placement Rules

- Put strategy/policy in `skills/`.
- Put execution/safety/storage in code.
- Search skills are layered as: core + domain (`jobs` or `people`), where `jobs` also carries preference overlay.

## Evaluate relatedness

```bash
python scripts/eval_relatedness.py
```

## Evaluate router

```bash
python scripts/eval_router.py
```
