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

After reading this guide, also read `docs/assistant_bridge/CODEX_CONTEXT.md` when it exists. That file is the lightweight assistant-facing overlay that evolution runs may update with current routing lessons and memory-intake guidance.

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

## Running Site Workflows

Codex Desktop or another main assistant coordinates CareerEng; it does not create a separate runtime host per site.

Before starting browser work, start the one workspace host in the local user terminal when it is not already running:

```zsh
cd /Users/alexlee/Desktop/gh/careerEng
.venv/bin/python -m careereng runtime-host serve
```

Do not search for its socket, invent another launch command, start a second host, directly launch another browser runtime, or switch to a provider backend when host access fails. If `careereng_runtime_host_status` reports a permission error, report that error; it does not prove the host is stopped.

1. Use `careereng_get_context` to inspect the active batch and target site before changing its execution state.
2. Multiple sites may run concurrently up to `agent.site_parallelism`. A paused, login-required, or CAPTCHA-required site does not block the other sites.
3. When the user completes a browser-only step, continue the same target site from its retained page and durable state. Do not restart unrelated sites or create another host.
4. The configured execution backend is fixed for a running host. Do not switch between provider and Codex execution during a run.
5. Use site-scoped pause, stop, or cancel when changing one worker. These commands revoke that work item's execution lease and do not stop sibling sites in the batch.
6. Treat `pause_unconfirmed` as transport uncertainty, not job failure. Resume reconstructs the worker from durable state instead of trusting the unconfirmed thread.
7. Ordinary phase completion keeps the same site work item, Codex thread, and browser. CareerEng advances phase context synchronously and automatically continues a non-terminal item if its current turn ends.
8. Internal heartbeat traffic is not a Desktop progress stream. Register the current main task and consume durable phase, attention, recovery, evolution, and terminal events through CareerEng. Registration alone does not start a push service.
9. For ranking-enabled sites, `ranking_pending` completes evaluation only; CareerEng materializes `ready_to_apply` and `deferred_by_rank`, then continues selected jobs to real application outcomes.
10. If bounded technical recovery is exhausted, report `waiting_user` rather than a job failure. A user continuation reissues the same durable work item, recreates only a dead scoped browser runtime when necessary, and resumes the current phase/item.
11. Recovery never reruns completed phases. Retrieval continues its saved checkpoint with dedupe; apply uses the frozen Apply List and reconciles an uncertain active item from that item's Job URL before continuing.
12. Prefer in-place continuation. When the original batch is terminal or non-reissuable and the user asks to continue rather than cancel, pass its `source_batch_id`; CareerEng creates one idempotent recovery batch whose starting phase/item is derived from that source checkpoint.
13. Evolution is non-blocking side work. A completed site releases its browser worker and the business batch remains terminal. The main agent handles durable evolution requests through the existing proposal, snapshot, apply, evaluation, and rollback flow.
14. External network/provider/service/browser-process interruptions enter checkpoint recovery and notification only. They do not trigger evolution unless later evidence explicitly diagnoses a CareerEng-internal defect.
15. After CareerEng accepts a job workflow, control its Codex workers only through CareerEng. Do not use Codex thread messaging, interruption, resume, or termination tools directly. Infrastructure diagnosis may inspect an underlying worker read-only; all state changes still go through CareerEng.
16. While the current main-agent turn is monitoring active work, call `careereng_wait_agent_events` with the last observed cursor. Handle returned events and acknowledge only the cursor actually processed. If user input interrupts the wait, read the durable inbox first on the next CareerEng turn.
17. CareerEng does not run the old App Server push/retry bridge. While active, the main task consumes the bounded wait interface; while idle, a user-authorized Desktop heartbeat wakes it to poll. A child's completion notification is separate and does not consume CareerEng events.

### Visible Desktop Launch And Monitoring

1. Check formal MCP/host reachability and current batch state. Do not use temporary Python wrappers to bypass a stale Desktop MCP connection.
2. For a new run, call `careereng_start_jobs_batch` with the user's scope; use `separate_batch` only when a separate new batch is requested. For explicit continuation, use the resume entry instead. Never confuse a retained task with a retained Apply List.
3. Obtain `careereng_list_worker_launch_specs`. Its `desktop_task` contract requires a visible Desktop task, not `spawn_agent`. For a new task, use Desktop `create_thread`, following its project/environment rules, with the supplied work-item prompt and a clear company title. For reuse, use the returned existing task ID; do not guess another task by site name.
4. Register the resulting ID and current main task ID through `careereng_register_native_worker`, including session ID and control epoch, and acknowledge the actual launch action. Confirm the task is accessible; expose its returned task link. Do not claim an action succeeded merely because a plan exists.
5. Before executing subsequent lifecycle actions, call `careereng_prepare_worker_action`. It rejects obsolete activity/epoch/binding plans. A `probe` is read-only task inspection, not interruption. Fresh tool/phase evidence can be reported through the state tool; an unchanged active label cannot be used to manufacture heartbeats. If Desktop lacks the required control operation, record a failed receipt and report it, without switching transports.
6. Read `monitor_policy` from context, launch specs, or monitor results. While supervising, poll the durable inbox with the explicit current `batch_id` and process control actions irrespective of notification timing. Before ending the turn, create/update a user-authorized Desktop heartbeat for this main task using `poll_interval_seconds`; never hard-code the user's progress interval as the liveness interval. Re-read the policy on each wake and update that same automation when its interval changes.
7. Render due `notifications` as compact summaries, then call `careereng_ack_notifications(delivery_id)`. Do not acknowledge before presentation. Repeated delivery is possible until acknowledged. Notification aggregation is durable and independent of the raw control-event cursor. Never summarize every raw phase event immediately, which bypasses throttling.
8. With a site filter, retain a local cursor at the last inspected event without globally acknowledging unseen other-site events. Without a filter, acknowledge the actual processed global cursor. Retain the local cursor and pending notification receipts across heartbeat wakes.
9. On completion, present final results, acknowledge the notification and pause the same heartbeat. Do not restart the batch, discard the reusable task, or submit again.

Configuration is in the project `config.toml`:

```toml
[agent.notifications]
progress_interval_seconds = 60
poll_interval_seconds = 60
```

Set `progress_interval_seconds` to 300 or 600 for five- or ten-minute ordinary
progress summaries. Quiet periods produce no notification. Attention, failures,
and completion bypass progress batching but are delivered on the next actual
poll, subject to Desktop scheduler/provider latency; this is not instant push.
Keep polling frequent for controls even when progress summaries are infrequent.
`poll_interval_seconds` is a positive whole number of minutes in seconds.
This configuration never changes the separate `[agent.recovery]` limits.

For direct lifecycle commands, see `docs/assistant_bridge/COMMANDS.md`.

## MCP Execution Tools

Use MCP tools in this order:

1. Inspect host and batch context with `careereng_runtime_host_status` and `careereng_get_context`.
2. For an active site task, call `careereng_get_work_item_context`.
3. Discover the current task's browser or state capabilities with its `careereng_work_item_list_*_tools` tool.
4. Execute only through the matching `careereng_work_item_*` tool. Pass the task context's `lease.context_revision` unchanged to every mutating browser/state call.
5. Finish a phase with `careereng_work_item_phase_result`. During apply, also pass the single current `scope.apply_target_job_ids` value unchanged; a stale revision or target is rejected and must not be retried against refreshed context.

Do not maintain a static list of browser controls in this guide. Browser and state tools are discovered from the current work item because they are scoped to its site and phase.

## Action Cards

When CareerEng needs Codex/user review instead of immediate execution, it may create an action card under:

```text
workspace/action_cards/open/
```

Use these commands:

```bash
python -m careereng action-card list
python -m careereng action-card show <card_id>
python -m careereng action-card close <card_id> --result "<summary>"
```

Action cards are review tasks. Do not treat them as automatic permission to modify files or run high-impact workflows.

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

For user-facing analysis, prefer this read order:

- Summary JSON/JSONL first: machine-readable facts and lifecycle state.
- Markdown reports second: quick human-readable views rendered from facts.
- Evidence last: events, traces, snapshots, metrics rows, and action cards for root-cause inspection.

Do not treat Markdown reports as a separate source of business truth. If a report and summary disagree, inspect the summary and evidence before answering.

Primary paths:

- `workspace/assistant_bridge/intake_events.jsonl`
- `workspace/assistant_bridge/action_events.jsonl`
- `workspace/assistant_bridge/correction_events.jsonl`
- `workspace/assistant_bridge/routing_examples.jsonl`
- `workspace/assistant_bridge/thread_state.json`
- `workspace/memory/profile_signals.jsonl`
- `workspace/memory/intent_signals.jsonl`
- `workspace/memory/application_feedback_signals.jsonl`
- `workspace/application_summary/application_summary.json`
- `workspace/metrics/`
- `workspace/reports/`
- `workspace/sites/<site>/events/`
- `workspace/interviews/events.jsonl`

## Career Memory Promotion

Assistant bridge stores raw intake events and typed raw signals. Long-term job-search memory is owned by `careereng/career_memory/`, not by the bridge.

Promote stored assistant signals into unified memory units with:

```bash
python -m careereng career-memory promote
```

Memory units are stored at:

```text
workspace/memory/memory_units.jsonl
```

When Codex can see a long current thread, it may curate the recent career-relevant messages into JSON/JSONL memory candidates and import them:

```bash
python -m careereng career-memory import-candidates /path/to/memory_candidates.jsonl
```

This keeps Codex responsible for thread-level understanding, while CareerEng validates, deduplicates, and persists local memory.

When the user asks to summarize or persist a dynamic number of recent assistant messages, keep that number as explicit evidence metadata instead of hard-coding a fixed window:

```bash
python -m careereng assistant import-recent /path/to/memory_candidates.jsonl \
  --limit 100 \
  --source-client codex \
  --source-thread <thread_id>
```

Use the requested number directly. For example, `总结最近 30 条对话` should use `--limit 30`; `总结最近 100 条对话` should use `--limit 100`.

`assistant import-recent` is the preferred wrapper for recent-N conversation intake. It imports the candidate file through the existing career-memory validator, records `workspace/assistant_bridge/intake_state.json`, and refreshes `workspace/assistant_bridge/context/latest.md`.

Before creating the candidate file:

- Read `workspace/assistant_bridge/context/latest.md`.
- Inspect existing `workspace/memory/memory_units.jsonl`.
- Inspect existing `workspace/evolution/browser_control/lessons.jsonl`.
- Import only missing, evidence-backed content.
- Use the existing categories: `profile_resume_signal`, `career_intent_strategy`, `application_feedback`, `correction`, `interview_record`, `evolution_lesson`.
- Do not invent a new schema for one new observation.

Split recent-N summaries before importing:

- Career profile, intent, application feedback, interviews, and reusable evolution lessons may become career-memory candidates.
- Current development tasks, next steps, boundaries, and verification instructions belong in the taskboard, not career memory.
- One-off process control, transient command chatter, and temporary debugging narration should be ignored unless it produced a durable lesson.
- Taskboard updates must be proposed to the user first. Only write them after explicit confirmation with `python -m careereng taskboard update <file>`.

The imported memory units should preserve:

- `facts.source_message_limit`
- `facts.source_client`
- `facts.source_thread_id`
- `evidence_refs[].scope`, such as `recent_100_messages`

First version rule: CareerEng does not automatically read Codex thread history. Codex curates the visible/recent thread context into candidates; CareerEng validates, deduplicates, and stores them.

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

## Evolution Outputs For Assistants

Assistant-router evolution may update:

- `workspace/assistant_bridge/routing_examples.jsonl`
- `docs/assistant_bridge/CODEX_CONTEXT.md`

Assistant-router evolution should only propose changes to this guide or `AGENTS.md`; it should not automatically rewrite those stable policy files.
