# AGENTS.md - CareerEng Assistant Rules

CareerEng is designed to be operated by Codex or another local AI assistant.

If the user uses `@career` or discusses job search, resumes, applications, interviews, target companies, career sites, reports, metrics, evolution, or CareerEng operations, read `docs/assistant_bridge/ASSISTANT_GUIDE.md`.

## Architecture Contract

For changes involving directories, tool declarations or implementations,
adapters, workspace state, browser control, orchestration, or evolution
boundaries, first read `docs/architecture/ARCHITECTURE.md`.

Treat that document as the target architecture during migration. Do not extend
legacy mixed modules merely because they are nearby. If a change alters an
architectural boundary, dependency direction, tool contract, or workspace
ownership rule, update the architecture contract and active taskboard before
implementing code.

## AI-First Design Boundary

CareerEng is an AI-first project. Before adding Python logic for workflow behavior, ask whether the behavior should be handled by the LLM through Skills, local memory, evidence packs, action cards, or an evolution proposal.

## Architecture First, Not Case-First Hacks

When modifying code, preserve the intended architecture before trying to make one failing live case pass.

Do not solve a local browser/application failure by adding narrow Python behavior in the nearest runtime file. First identify the proper layer:

- `job_flow.py` should orchestrate phases, apply lists, state transitions, and calls into supporting engines.
- `careereng/evolution/` should own reusable evolution loop mechanics, proposal contracts, evidence handling, validation, and promotion/rollback flow.
- Skills and LLM/Codex proposals should own site workflow, form-filling behavior, matching policy, and business judgment.

If a fix starts making `job_flow.py`, browser runtime, provider code, or storage code decide site-specific workflow strategy, stop and redesign it as one of:

- A Skill change
- An evolution lesson
- A proposal/action-card workflow
- A generic framework hook that lets the LLM decide the business action

Never trade architecture correctness for a quick local pass. A change that makes one site work by hiding business logic in Python is considered a regression unless the user explicitly approved that runtime abstraction.

Use Python for framework responsibilities:

- Orchestration
- Persistence
- Validation
- Safety gates
- Metrics
- Recovery plumbing
- Evidence packaging
- Patch application and rollback

Use LLM/Skills for business intelligence:

- Site workflow decisions
- Job matching policy
- Form-filling strategy
- Status interpretation
- Evolution/refinement of Skills
- Adapting from previous failures

Do not hard-code website-specific behavior, browser semantics, business policy, or form strategy in Python when the same behavior can be expressed as a Skill, memory/evolution lesson, or LLM-generated patch. For loop/evolution work, Python should create the conditions for the LLM to reason and improve the workflow, not replace that reasoning with fixed runtime rules.

For evolution loops, prefer the existing evidence/candidate/action-card/memory/summary flow. Python may carry an `evolution_decision` between batches, but the decision content should come from LLM/Skill loop-control evidence. Do not add narrow Python subclasses or site-specific branches when a Skill or evolution proposal can express the change.

An evolution loop is not complete when only evidence, an action card, or a generic refinement hint exists. The runner must not continue a failed job or follow-up batch as an evolved attempt until a concrete proposal exists, such as `run_local_overlay`, `skill_patch`, `routing_example_append`, `memory_unit_append`, or `assistant_context_update`.

## Do-Not-Do List

- Do not interrupt a live browser workflow while the user is observing it unless the user explicitly asks you to stop or a destructive action is imminent.
- Do not treat action cards, evidence, or generic guidance as completed evolution.
- Do not bypass existing evolution, lesson, taskboard, memory, or action-card infrastructure with one-off local logic.
- Do not treat "it ran once" as "evolution succeeded"; check validation evidence from follow-up runs.
- Do not demote a required Codex/LLM proposal step into manual hard-coded Python behavior.
- Do not implement site workflow, matching policy, form-filling strategy, or browser business judgment in Python when it belongs in Skills, lessons, or an LLM-generated proposal.

## Assistant Context Pack

For `@career`, memory, evolution, recent conversation summaries, action cards, CareerEng status, or current development taskboard work, read this generated context first:

```text
workspace/assistant_bridge/context/latest.md
```

If it does not exist or looks stale, generate it before answering:

```bash
python -m careereng assistant context
```

This context pack summarizes the current taskboard, local memory, accepted lessons, open candidates, action cards, metrics, reports, and git dirty files. Use it as the first local map, then inspect the underlying evidence files when details matter.

## Taskboard

When listing development tasks, prefer this structure:

- `Now`: current work to advance in the next one or two assistant turns.
- `Next`: confirmed follow-up work for the current stage.
- `Later`: longer-term direction that should not be lost.
- `Parking Lot`: useful ideas that are explicitly not part of the current work.

When discussing tasks with the user, a simple numbered list such as `1, 2, 3` is fine because it is easier to review and revise in conversation. When saving tasks into the taskboard, convert that discussion list into the `Now / Next / Later / Parking Lot` structure instead of copying the numbered list verbatim.

`workspace/taskboard/current.md` is the compact active work set. Do not append
long progress logs or complete historical plans to it. `taskboard update`
replaces the active board and preserves the prior version in
`workspace/taskboard/history/`; archive only a completed or superseded board.

For development tasks saved into the taskboard, include these fields when useful:

- `Goal`: why the task exists.
- `Touch`: expected files or modules to modify.
- `Do Not Touch`: explicit boundaries and risky areas to avoid.
- `Verify`: checks or commands that confirm completion.

Do not automatically save every task list. Only write to `workspace/taskboard/current.md` when the user explicitly asks to save, remember, update, or record the taskboard.

Use taskboard commands when requested:

```bash
python -m careereng taskboard show
python -m careereng taskboard update <file>
python -m careereng taskboard done <index>
python -m careereng taskboard archive
```

## Assistant Bridge

Use the assistant bridge before guessing commands:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "<user message>"
```

Use the returned `suggested_command` unless local evidence shows it is wrong.

Do not auto-execute high-impact CareerEng commands, especially job application runs, unless the user explicitly asks or confirms.

## Local Evidence

Before answering from memory, inspect local evidence when relevant:

- `workspace/reports/jobs/`
- `workspace/jobs/batches/`
- `workspace/sites/<site>/jobs/history_jobs.json`
- `workspace/sites/<site>/jobs/runs/`
- `workspace/metrics/`
- `workspace/action_cards/`
- `workspace/memory/`
- `workspace/evolution/`

## Common Assistant Workflows

- For application status or job-search requests, route through `assistant ingest` first.
- For report or summary questions, prefer reading local reports and application summaries before rerunning browser automation.
- For site Skill changes, make small targeted edits under `skills/search/jobs/sites/<site>/SKILL.md`.
- For project-wide job-search policy changes, edit `skills/search/jobs/SKILL.md`.
- For evolution work, inspect `docs/evolution/` and the relevant evidence pack before changing Skills or runtime behavior.
- For action cards, use `python -m careereng action-card list` and inspect the card before modifying files.

## Boundaries

CareerEng owns durable state, command execution, reports, metrics, history, and safety gates. Codex can inspect files, explain behavior, propose changes, and make targeted edits with user approval.

Keep Python focused on orchestration, persistence, safety, metrics, and rollback. Keep site behavior and business policy in Markdown Skills where practical.

Do not generalize from one company's career site into Python runtime behavior. A single NVIDIA, Workday, Microsoft, Qualcomm, AMD, Apple, or other site observation is not enough reason to hard-code browser semantics, selectors, business rules, matching logic, or phase behavior in Python.

For job workflows, preserve these phase boundaries:

- `job_retrieval` records the job list with the smallest stable fields available, such as title, URL, site job ID when visible, location, and posted label.
- `job_retrieval` must not open job detail pages just to enrich JD text, decide fit, or compensate for incomplete history.
- Missing JD or missing site job ID should not block retrieval pagination when a stable job URL exists.
- JD reading, fit scoring, application decisions, form filling, and submission belong in `apply` and site/project Skills.
- Python may provide generic storage, deduplication, URL normalization, safety guards, and recovery prompts. Python must not encode site-specific navigation or business policy unless explicitly approved.
