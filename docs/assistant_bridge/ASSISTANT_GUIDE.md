# Assistant Bridge Guide

CareerEng can be called by external AI assistants such as Codex, Claude Code, Cursor, or future memory/router systems.

The bridge is intentionally generic. Do not treat it as Codex-only.

## When To Use The Bridge

Call the assistant bridge when the user message is about:

- CareerEng commands or workflows.
- Job search, target companies, career sites, applications, or application status.
- Resume, CV, persona, profile, or project progress that may affect job search.
- Career intent, target roles, learning plans, role strategy, or capability gaps.
- Application feedback such as rejection, in-process status, interview signals, or matching strategy.
- Interview preparation, interview records, or interview follow-up.
- User corrections about a CareerEng route, action, or stored memory.

Use:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "<message>"
```

Replace `codex` with the active assistant client if needed, for example `claude-code`, `cursor`, or `other`.

## Explicit Trigger

`@career` is the explicit trigger.

Examples:

- `@career 检查投递状态`
- `@career 总结一下投递情况`
- `@career 我想投 AI infra，需要补什么？`
- `@career 帮我准备 NVIDIA SDET 面试`

When a message starts with `@career`, send it to `assistant ingest`.

## Thread Scope

For multi-turn career conversations, the user should start a new assistant thread and begin with `@career`.

An explicit `@career` message can open a CareerEng scope for the current thread when it is about:

- profile/resume signals
- career intent or strategy
- application feedback
- interview records

Follow-up messages in that same dedicated thread can be sent to `assistant ingest` even if they do not repeat `@career`.

In a mixed software-development thread, do not automatically inherit CareerEng scope. Only ingest explicit `@career` messages unless the user clearly confirms that the thread is now a CareerEng career conversation.

Close scope with:

```bash
python -m careereng assistant end --client codex --thread <thread_id>
```

or by ingesting:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "@career end"
```

## Execution Rule

The bridge returns structured JSON with:

- `data_category`
- `suggested_action`
- `suggested_command`
- `should_save`
- `should_execute`
- `thread_scope`

First version rule:

- Save and classify relevant messages.
- Suggest commands.
- Do not automatically execute high-impact commands unless the user explicitly asks and the assistant is already allowed to run project commands.
- If there is no `@career` and no active scope, use the bridge for suggestion first.

## Data Categories

The bridge classifies data into six first-version categories:

- `careereng_command`: user wants CareerEng to do something.
- `profile_resume_signal`: resume, CV, profile, capability, or project progress signal.
- `career_intent_strategy`: target roles, target companies, role strategy, learning plan, or career direction.
- `application_feedback`: application outcome, status, rejection, in-process, or strategy feedback.
- `correction`: user correction for wrong route/action/memory.
- `interview_record`: interview preparation, live interview notes, or interview follow-up.

## Local Source Of Truth

CareerEng local files are the source of truth.

Adapter backends may classify, summarize, or retrieve long-context memory, but they must write results back into CareerEng local storage.

Primary paths:

- `workspace/assistant_bridge/intake_events.jsonl`
- `workspace/assistant_bridge/action_events.jsonl`
- `workspace/assistant_bridge/correction_events.jsonl`
- `workspace/assistant_bridge/routing_examples.jsonl`
- `workspace/assistant_bridge/thread_state.json`
- `workspace/memory/profile_signals.jsonl`
- `workspace/memory/intent_signals.jsonl`
- `workspace/memory/application_feedback_signals.jsonl`
- `workspace/interviews/events.jsonl`

## Adapter Boundary

Processor adapters are pluggable.

The default adapter is local rule-based classification. Future adapters can use stronger long-context or typed-memory systems.

The adapter may help with:

- classification
- semantic labels
- detected entities
- signal extraction
- routing example generation
- thread summarization

The adapter must not own CareerEng storage, command execution, or business history.

