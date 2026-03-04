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
- Site workflow with skill gate (search-only if site skill missing)

## Install

```bash
pip install -e .
```

## Configuration

Run any command once; CareerEng auto-creates:

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

[providers.openrouter]
api_base = "https://openrouter.ai/api/v1"

[providers.openai]
api_base = "https://api.openai.com/v1"
```

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
careereng run -m "我想找中国后端岗位" --session cli:default
careereng resume upload --file ./cv.md --session cli:default
careereng report list
careereng report review --id profile_report_xxx
```

## Repository Layout

```text
careerEng/
├─ skills/
│  └─ resume-sync/SKILL.md
├─ evals/
│  ├─ relatedness/
│  │  ├─ few_shot.yaml
│  │  ├─ evaluator.yaml
│  │  └─ dataset.jsonl
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

## Workspace Layout

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
├─ sites/
│  └─ <site_id>/
│     ├─ site.json
│     ├─ jobs/catalog.jsonl
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

When user asks to search/apply a company:

- Register site folder under `workspace/sites/<site_id>/`
- Search jobs with Playwright
- If site skill is missing: search only
- If skill exists and user requested apply: ask `y/n` then apply

## Evaluate relatedness

```bash
python scripts/eval_relatedness.py
```
