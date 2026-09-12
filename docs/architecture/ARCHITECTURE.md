# CareerEng Architecture Contract

## Purpose

This document is the architectural entry point for humans and AI assistants.
Read it before changing module boundaries, tools, adapters, workspace state,
browser control, evolution, or workflow execution.

It defines the target architecture. Existing mixed modules are compatibility
code during migration, not a reason to extend the old boundaries.

## Identity And Lifecycle Reconciliation

`career/applications/identity_links.py` owns reversible, site-scoped identity
associations under `workspace/jobs/identity_links/`. Workers decide equivalence
from evidence through the scoped `job_identity` state tool. Python validates
and persists the association with an existing canonical job ID; it must not
infer equivalence from company-specific ID prefixes or titles. History reads
may resolve an association, but original records and frozen plans are retained.
Revocation affects future resolution, not historical evidence.

`orchestration/worker_control` owns terminal-state guards and action fencing.
Desired pause is separate from confirmed runtime suspension and browser release.
Late pause/report messages cannot demote completed business work; terminal work
may still require resource cleanup. Explicit resume uses the existing command
and control-epoch path, not an automatic transition out of waiting_user.
Metrics distinguish inherited history skips from evidence of live application work.
Workspace file transaction exclusion is provided by `platform/persistence/mutex.py`;
worker registries and action/command stores share locks across instances and processes.
This protects technical updates, not site policy, and does not hold a workspace-wide
lock while browser operations execute.

Native worker state reports require `expected_control_epoch` from the current
launch spec or control result. Validation occurs inside the registry transaction;
stale observations cannot complete or reactivate a newer lease.

Resume adapters must not reissue work-item leases before the Runtime Host has
resolved the requested scope. The host reissues reusable items or invokes the
existing checkpoint-recovery owner for released/terminal items, returning the
resolved batch ID. Native actions are restricted to active items in that batch;
an old same-site binding must not be revived by a successful recovery elsewhere.

## Two Separate Trees

```text
skills/       Declarative LLM behavior and site policy.
workspace/    All user data, runtime state, generated artifacts, and temporary files.
careereng/    Python source code, schemas, contracts, and implementations.
```

`careereng/` never stores user runtime data. `workspace/` is the only runtime
data root, including browser profiles, snapshots, caches, session state,
history, reports, taskboard records, and evolution artifacts.

## Target Source Layout

```text
careereng/
  career/                       Career-domain capabilities
    applications/               Search, review, apply planning, application history
    resume/                     CV generation, parsing, variants, exports
    profile/                    Profile and career intent capabilities
    interviews/                 Interview capabilities
    memory/                     Career-focused memory capabilities

  evolution/                    Evidence, proposals, lessons, patches, reviews, loops
    artifacts/                  Workspace-path owners for evidence, candidates, proposals, reviews, and summaries
    evidence/
    proposals/
    lessons/
    reviews/
    patches/
    loops/
    work_items/                 Evolution work items; includes action-card behavior

  orchestration/                Generic progression of units of work
    engine/                     Batch, item, phase, continuation, resume progression
    context/                    Current-unit Skill, memory, and continuation context
    agent_protocol/             Agent-visible contracts and tool declarations
    commands/                   Generic tool-call dispatch to owning capabilities

  platform/                     Shared technical infrastructure
    persistence/                Workspace access, stores, indexes, versioned documents, normalization, backups
    runtime_host/               Workspace-scoped browser/session owner and versioned local host protocol
    web_control/                Browser runtime, profiles, MCP gateway, raw web operations
    sessions/                   Runtime ownership, session lifecycle, recovery plumbing
    reporting/                  Generic report artifact writing, indexes, events, snapshots, render helpers
    observability/              Metrics, traces, and generic operational summaries
    cache/                      Workspace-backed reusable runtime artifacts and compatibility indexes
    project_state/              Taskboard and assistant/project-level state
    maintenance/                Cleanup, repair, diagnostics

  adapters/                     External protocol adapters only
    bootstrap.py                Application composition root for CLI/MCP/host adapters
    providers/                  OpenAI, Anthropic, and other API transports
      browser_phase_runtime.py  Responses API browser-tool execution adapter
    mcp/                        CareerEng MCP server transport
    cli/                        Command-line transport
    host/                       Deprecated compatibility exports for the runtime host
    external_agents/            Generic work-order audit/recovery and future external-agent contracts
    assistant_bridge/           Conversation ingestion and assistant-context transport

  config/                       Configuration loading and validation
```

The actual migration can be incremental. New code must follow this layout;
legacy files should be reduced or moved rather than becoming new permanent
extension points.

The legacy top-level `agent/`, `core/`, `browser_controls/`, `storage/`,
`tools/`, and compatibility packages have been retired. A few migrated modules
remain intentionally large while their internals are decomposed in follow-up
work: `orchestration/engine/job_flow.py`,
`orchestration/engine/browser_automation.py`, and
`adapters/providers/browser_phase_runtime.py`. The orchestration runner
receives its concrete provider runtime from `adapters/bootstrap.py`; new
behavior must be added to the owning sub-capability rather than expanding
these files further.

## Compatibility During Migration

Legacy module paths remain thin compatibility exports while callers migrate.
The current source owners are `career/applications/` for application summary,
history-repair, and job-report projections; and `adapters/` for providers,
MCP, CLI, assistant bridge, and external-agent bridge transports. Compatibility
modules must alias or re-export the owning implementation only; they must not
gain new business behavior.

## Workspace Ownership

The physical data layout remains independent from Python package layout.

```text
workspace/
  cv/ profile/ intent/ interviews/ memory/
  jobs/ applications/ sites/
  evolution/ action_cards/
  taskboard/ sessions/ metrics/ reports/
  cache/ tmp/ debug/
```

The first migration does not require moving every existing workspace path.
`platform/persistence/` provides the stable access layer so physical paths can
be normalized later without leaking path knowledge through the codebase.

Versioned project documents use the same physical lifecycle where applicable:

```text
workspace/<domain>/current.md     # Compact active state
workspace/<domain>/history/       # Immutable pre-replacement snapshots
workspace/<domain>/events.jsonl   # Lifecycle events
workspace/<domain>/archive/       # Completed or superseded documents, when needed
```

The persistence primitive owns replacement, snapshots, events, and archival.
Each domain owns its own document schema, rendering, and business transitions.
For example, `workspace/taskboard/current.md` is an active work plan, not an
append-only implementation log; detailed progress belongs in history.

## Runtime Cache Boundary

`platform/cache/` persists generic runtime artifacts in `workspace/cache/`:
artifact payloads, a compact index, and immutable cache events. Its supported
artifact kinds are runtime capabilities, phase context, mappings, and explicit
browser sequences. The platform only checks structural compatibility (scope
and declared dependency-version equality); it does not decide that a cached
artifact is semantically safe for a live page.

The active site worker receives compact compatible candidates in phase context
and can use `cache_lookup`, `cache_read`, `cache_propose`, and
`cache_validate`. A new artifact remains a provisional candidate across
same-site batches; a hit is never an instruction to act without current
live-page validation. The worker must provide reuse rationale, preconditions,
page fingerprint, expected benefit, and evidence when proposing a candidate.
The LLM/Skill decides whether to read, reuse, validate, stale, or retire an
artifact. Cache validation events are indexed into evolution evidence packs so
later user-approved evolution can decide whether to promote a repeated result
into a lesson, Skill patch, or infrastructure proposal.

## Durable Work-Item Boundary

Every external-agent task has one persisted `work_item_id`, indexed in
`workspace/agent_bridge/work_items/index.json`. MCP resolves authorization and
scope from that index plus the payload, never from mutable browser-session
metadata. Browser sessions may display the current work order, but losing or
refreshing that display field must not invalidate an active task. Work-item
lifecycle events are append-only in the same workspace directory; a worker
turn, phase transition, and user wait refresh the existing item rather than
creating a new task identity.

`platform/observability/execution_diagnostics.py` records objective execution
facts such as inactivity recovery, browser/transport errors, and checkpoints.
They are exposed as an on-demand work-item resource. Python records and scopes
the evidence; the agent decides whether it indicates user input, retry-later,
recovery, exploration, or a proposal.

## Batch Resume Snapshot Boundary

An apply-enabled batch locks the current exported resume before any site worker
starts. The resume capability creates one immutable batch artifact plus one
site-isolated upload copy under `workspace/tmp/browser_controls/`, records the
filename, content hash, version, and scoped paths in the batch, and carries the
site copy into every work item from its first phase. A reused unfinished batch
keeps its original resume version when another site is appended.

Workers may upload only the staged path declared by their current work item.
The runtime host validates `browser_file_upload` calls before browser side
effects, while Skills and the LLM continue to decide when a site's live page
requires a resume upload. Mid-batch resume replacement is intentionally not
supported; a newly exported resume is selected by the next new batch.

## Ranked Application Queue Boundary

Sites that must compare all eligible jobs before submission declare that policy,
the ranking limit, and any queue grouping in their Skill. The worker owns live
JD interpretation and records eligible rows as `ranking_pending` with scores;
Python does not infer site policy or job fit.

`career/applications/ranked_queue.py` owns only generic validation and
deterministic queue materialization. Once every apply-plan row has completed
review, it marks the selected rows `ready_to_apply` and the remainder
`deferred_by_rank`. The orchestration engine then runs only selected rows through
submission. `application_status` remains reserved for observed application
outcomes, so ranking, deferral, rejection, and submission cannot be conflated.

Queue materialization is persisted and idempotent. A resumed worker treats
`ready_to_apply` as a submission target without repeating matching, while
`deferred_by_rank` is complete for the current batch but is not a historical
application outcome.

## Site Mode And Evolution Boundary

Site Skill front matter carries one structural execution mode and one separate
user authorization flag:

- `status: draft` means the site has only been initialized. It cannot execute
  until an agent has made the site strategy runnable.
- `status: exploration` means the site executes through the shared evolution
  loop engine. It is used for new-site discovery or an explicitly requested
  re-exploration of an existing site.
- `status: ready` means the site executes its normal workflow. A configured
  site-run threshold may still start the same shared loop engine for
  refinement; a ready site does not need to be demoted to exploration.
- `apply_enabled` is independent of `status`. It is the user's authorization
  for real application submission, not a proxy for Skill maturity.

All evolution paths use one loop-engine contract: evidence, proposal,
materialized change, validation, and synthesis. Configuration supplies
only structural limits and trigger cadence. Codex/another agent, guided by
Skills and evidence, chooses what changed, whether it worked, and whether a
site is ready to stop evolving. Python persists state, enforces scope and
limits, and advances the declared lifecycle without encoding site policy.

When an exploration batch reaches a terminal site result, orchestration creates
an action card, evidence pack, and Codex solution request before any readiness
transition. The owning site worker consumes that request as its next turn on
the retained thread, then applies the proposal and either starts the existing
follow-up batch path or finishes. The proposal must carry an explicit
`site_mode_update` decision (`ready` or `exploration`); applying it snapshots
the target Skill front matter. This handoff is triggered only for the terminal
batch being processed and never retroactively rewrites historical batches.

Every browser phase exposes `record_evolution_signal` through the shared
agent protocol. A worker supplies the failure pattern, evidence, refinement
request, and optionally an explicit `run_local_overlay`. The loop engine
records that input through the existing evidence, candidate, action-card, and
memory stores. A materialized overlay in `EvolutionMemoryStore` is the only
run-local execution source; legacy apply-loop summaries remain historical
records and are never injected as strategy. An overlay is injected only into
the next work item for the same batch, site, and phase. This keeps exploration, refinement, API
providers, and external-agent workers on one contract rather than creating an
apply-only evolution path. At the outer boundary, synthesis reads the generic
site evolution container and closes all active run-local scopes for that site
batch after the applied synthesis has consumed their evidence.

## Report Artifact Boundary

`platform/reporting/` owns only the generic mechanics of report artifacts:
writing JSON/Markdown, maintaining `workspace/reports/index.jsonl`, recording
write events, and optionally snapshotting a replaced artifact. It never imports
career stores or interprets an application, job, or evolution outcome.

Career application reports, application summaries, evolution reports, and
platform metrics each build their own payload and Markdown projection, then use
the shared artifact store. Their existing output paths remain domain-owned;
the report index is a cross-domain discovery aid rather than a replacement for
the source artifact layout.

Job reports present metrics primarily per `site_key`: each site report contains
its own token, phase, tool, snapshot/retry, cache, and outcome aggregates. The
batch report retains only a cross-site aggregate and batch wall-clock. Site
durations are never summed and presented as the wall-clock duration of a
parallel batch.

## Runtime Session Boundary

`platform/sessions/` owns persistent session messages/state, browser-profile
locks, and generic retained browser runtime lifecycle. Its runtime registry
starts, reuses, releases, and protects a local browser MCP process for a
caller-provided profile; it does not know a site's phase sequence, matching
policy, or browser outcome semantics.

Profile release is a single generic lifecycle operation. After all processes
using the exact dedicated profile have stopped, it removes only that profile's
orphaned Chromium `SingletonLock`, `SingletonSocket`, and `SingletonCookie`
entries, then releases the CareerEng ownership record. It must never remove
locks while a process still uses the profile, and it must report whether
resources were actually released.

Legacy browser phase runners may retain a thin compatibility method that
supplies the site profile and writes domain session status. They must delegate
runtime ownership to `platform/sessions/` rather than maintain their own
active-process map or profile-lock lifecycle.

`SiteWorkerSession` is a separate continuity boundary for external agents. It
may retain one agent thread across inner-loop attempts, user pauses, and
eligible consecutive site batches. A phase completion, a single batch
completion, or `waiting_user` does not destroy that thread. Browser runtime
release is independent from thread retention. A worker session ends only on
explicit session close, a declared review/loop boundary that has completed its
outer synthesis, or confirmed unrecoverable transport loss; a replacement
thread then resumes from persisted CareerEng state.

`batch` is the durable unit of a user run. While it is unfinished, additional
sites join that batch; a new site does not create a new batch merely because it
uses exploration, a different browser profile, Codex, or a provider. A site
worker is host-local and temporary. Its in-memory capacity wait list is not a
durable workflow status: after a host restart, the host rebuilds eligible work
from persisted unfinished batch/site records and their current work item.
Only the user can request an explicitly isolated batch, or a new batch begins
after the prior one has ended.

A batch is a run group, not a site lifecycle gate. Each site independently
owns retrieval, application, `waiting_user` resume, exploration synthesis,
and its effective-run counter. When one site finishes browser work, its Codex
thread performs that site's summary immediately while other sites in the same
batch continue. A site summary is therefore represented on its site row and
never changes the whole batch to `waiting_solution`. Only after every site has
settled does the batch produce its aggregate report and become releasable.

## Runtime Host Boundary

`platform/runtime_host/` owns the workspace-scoped local process boundary for
browser/session execution. One host owns the workspace runtime and delegates
per-site browser/profile access to `platform/sessions/` and
`platform/web_control/`; it does not own site policy or create one process per
site.

One healthy host can serve several unfinished batches and several concurrent
site workers up to configured capacity. A completed, cancelled, or otherwise
non-resumable site releases only its own worker and browser runtime. A host
closes only after the workspace has no unfinished batch. If a host disappears,
its successor reuses the durable batch/site/current-work-item records and the
eligible retained agent thread; a host restart never creates a new batch or
rotates a thread by itself.

`agent.site_parallelism` is the one configured site-worker limit for every
backend. Provider workers use it to bound remote LLM/browser phase work;
Codex workers use it to bound site-specific Codex threads. Provider rate
limits remain adapter transport concerns and never change batch membership.

Its versioned protocol is intentionally generic: `ping`, batch/resume/pause,
browser/state tool transport, and `release_site`. `release_site` accepts only
runtime lifecycle identifiers such as `site_key`; it releases one retained
site runtime/profile without reading or interpreting jobs, applications,
Skills, matching, or batch policy. Every response includes a protocol version.
MCP and external-agent adapters connect to an already user-owned host and must
not start a browser-owning process from a constrained desktop sandbox. A
missing or stale host is a recoverable infrastructure condition, reported as
`runtime_host_unavailable` or `runtime_host_protocol_mismatch`, never as a job
or site failure.

## Main-Agent Events And Live Status

Concurrent site workers never write directly into the Codex Desktop
conversation. They report lifecycle facts through CareerEng. The shared event
store persists a compact, append-only main-agent inbox at
`workspace/agent_events/events.jsonl`; Desktop maintains its own acknowledgement
cursor there. This persistence is authoritative, so a Desktop restart or a
temporarily unavailable callback receiver cannot lose a user-required event.

Events carry site, batch, thread, turn, phase, URL, summary, and one attention
classification:

- `action_required`: user browser/profile action such as sign-in, CAPTCHA, or
  missing information.
- `review_required`: bounded recovery is exhausted or the worker needs a user
  decision.
- `notification`: site or batch completion and report availability.
- `audit`: detailed execution facts that stay outside the default Desktop inbox.

Heartbeat and raw transport activity remain internal runtime evidence and are
not forwarded as Desktop conversation noise. Durable phase changes,
waiting-user states, exhausted recovery, and terminal milestones are the
user-facing event boundary. The registered main agent receives those events;
site worker threads receive only scoped execution and continuation prompts.

`careereng_list_agent_events`, `careereng_wait_agent_events`, and
`careereng_ack_agent_events` are the read, long-poll, and consumption tools for
this inbox. A main agent that already owns the active Codex turn long-polls the
queue instead of requiring a second writer to inject another turn. User input
may interrupt that wait; the next turn resumes from the durable cursor.
`careereng_get_agent_status` is separate: it reads the host's current per-site
worker/browser state and answers what is running now. It is not a batch
projection and it does not replace durable events.

`careereng_register_main_agent` persists the main task identity; it does not
start an App Server callback. The old `adapters/codex/main_agent_bridge.py`
transport is removed. An active main task polls; a user-authorized Desktop
heartbeat wakes an idle main task to poll. Child completion notifications are
not a substitute for this durable inbox.

`platform/project_state/notifications.py` owns a separate durable aggregation
projection with offered notification IDs and presentation acknowledgements.
Ordinary phase events are grouped per batch/site using
`agent.notifications.progress_interval_seconds`; urgent attention, failures,
and completion bypass that interval. Raw control events are never throttled.
The Desktop heartbeat reads `poll_interval_seconds` from MCP `monitor_policy`,
independently of progress batching and recovery timing. Urgent delivery means
the next actual poll, not instantaneous push. Pending notification data survives
raw event acknowledgement, restart, and a change to the configured interval.

CareerEng uses one main-agent controller per workspace and any number of
site-scoped workers across one or more batches. The main task explicitly
registers a concrete controller thread; a different thread cannot silently
replace it. Events carry a monotonic workspace sequence and their batch, site,
worker-thread, turn, and phase identities. Registration records an event
watermark so reconnecting a Desktop does not replay obsolete historical
notifications, while unresolved events created after registration remain
retryable until acknowledged.

The control boundary is task-scoped rather than global. Once CareerEng accepts
a job-search, status-review, matching, application, recovery, or related
evolution workflow, the main agent controls its workers only through CareerEng
lifecycle, command, status, and event tools. It must not use Codex thread tools
to message, interrupt, resume, or terminate those managed workers directly.
Read-only inspection of an underlying Codex worker is allowed only while
diagnosing CareerEng infrastructure; state changes still go through CareerEng.

Evolution is side work, not a business-batch phase. A terminal site releases
its browser worker after persisting its result even when an evolution solution
request is created. The main agent receives durable `evolution.requested`,
`evolution.resolved`, and `evolution.failed` events and drives the existing
evidence/action-card/proposal/snapshot/evaluation/rollback flow. Pending
evolution never changes an otherwise terminal batch back to `running` or
`waiting_solution`.

Exploration readiness uses version-scoped consecutive full-cycle evidence.
Three successful exploration cycles make readiness review due; an external
interruption is neutral, and a confirmed internal failure resets the streak.
Ready sites create a non-blocking evolution solution request after every five
new effective full runs. Confirmed internal defects may trigger earlier;
external network, provider, browser-process, or service-capacity interruptions
only enter checkpoint recovery and operational notification. Unknown-origin
failures remain evidence until their origin is established.

CLI may explicitly run the lifecycle commands:

```text
python -m careereng runtime-host serve
python -m careereng runtime-host status
python -m careereng runtime-host stop
python -m careereng runtime-host release-site --site <site_key>
```

The old `adapters/host/workspace_manager.py` and hidden `manager-serve` command
are compatibility shims only. Do not add new behavior there.

## CLI Adapter Loading

`adapters/cli/` is an external transport boundary, not a mixed implementation
module. Command groups live in focused `*_commands.py` modules and import only
their owning capability. The entrypoint routes by the requested command group
so lightweight commands such as `runtime-host status` and
`runtime-host release-site` do not import career history, workflow, evolution,
resume, or interview modules.

The CLI groups are adapters only. They call shared platform/career/evolution
contracts and do not reimplement those operations. `commands.py` is a thin
compatibility aggregator for callers that import its Typer app; it must not
receive command implementations or business helpers.

The independently routed groups currently cover runtime lifecycle, project
state, profile/resume/career-memory, interviews/capture, assistant and
external-agent bridge operations, MCP hosting, and evolution work items/runs.
Application summary, report, site-registry, and non-runtime batch-management
commands are also routed independently. The remaining job-execution commands
stay in `commands.py` only until their owning adapter is extracted. A command group
may import its owning domain capabilities, but must never import a sibling CLI
adapter or depend on `commands.py` for implementation.

## Persistence Access Boundary

Generic JSONL and versioned-document primitives are imported directly from
`platform/persistence/`. Legacy `storage/jsonl.py` and `storage/domain_store.py`
remain compatibility exports only. Domain stores still own their workspace
schemas, paths, and semantic state transitions; platform persistence must not
centralize those decisions.

JSONL primitives provide forward and reverse bounded iteration, but do not
interpret rows. Application history remains owned by `career/applications/`:
its canonical job records stay in the site history document, while a derived
site-local activity index supports recent-observation reads. Observation time
is never treated as a job publication date.

## Evolution Work Items

`evolution/work_items/` owns durable, assistant-facing evolution work items.
Action cards are the first migrated work-item implementation: their schema,
store, renderer, and Skill-bootstrap/refinement helpers live there, while
`careereng/action_cards/` remains compatibility exports only. The migration
does not move or rewrite `workspace/action_cards/`; its existing
`open/done/cancelled`, index, and event lifecycle remains the contract.

## Ownership Boundaries

| Area | Owns | Must Not Own |
| --- | --- | --- |
| `career/` | Career-domain operations and semantic models | Browser lifecycle, adapter protocol, generic persistence mechanics |
| `evolution/` | Evidence, proposal, validation, lesson, patch, rollback mechanics | Site-specific decisions in Python, browser transport |
| `orchestration/` | Progressing work items and phases; assembling context; continuation | Job-fit decisions, site form policy, direct provider-specific behavior |
| `platform/` | Generic persistence, report artifacts, browser/runtime resources, sessions, observability | Career policy, matching decisions, site workflow strategy |
| `adapters/` | External request/response and protocol translation | Business state transitions, Skills, matching, persistence policy |
| `skills/` | LLM/site workflow, matching, form strategy, status interpretation | Python runtime implementation |
| `workspace/` | Runtime data and generated artifacts | Python source code |

An external agent completing a declared phase sequence emits a generic
`phase_sequence_completion` signal. The browser layer does not choose a
follow-up domain operation. `orchestration/` consumes that signal and invokes
the relevant `career/` or `evolution/` capability, which may schedule the next
persisted work item. This keeps raw browser control independent of job plans
and other business state.

Backend-neutral phase progression belongs in
`orchestration/engine/phase_orchestration.py`. Provider loops and external
agent workers must consume this shared state rather than reimplement phase
completion behavior. For example, a retrieval history-stop result is evidence,
not a terminal command: the shared engine tracks any required confirmation
progress while the active Skill remains responsible for site pagination and
workflow policy.

For `native_agent`, creating a work order is not sufficient to call a batch
agent-active. CareerEng exposes durable launch specifications, and the Codex
Desktop main Agent creates each worker through the already-running Desktop App
Server. The main Agent is the sole worker supervisor. CareerEng never starts a
second Codex App Server and no worker creates or controls another worker.

Python provides orchestration, persistence, validation, safety, recovery
plumbing, metrics, evidence packaging, patch application, and rollback.
LLM/Skills provide business reasoning, matching policy, site workflow, form
strategy, status interpretation, and adaptive evolution decisions.

## Dependency Direction

```text
adapters -> orchestration -> career / evolution -> platform
                         -> platform

skills + workspace <-> owning domain through platform interfaces
```

Rules:

- `platform/` never imports `career/`, `evolution/`, or `adapters/`.
- `adapters/` do not implement workflow progression or write business state
  directly; they invoke shared orchestration contracts.
- Cross-domain behavior is coordinated by `orchestration/`, not by circular
  imports between `career/` and `evolution/`.
- Site-specific policy belongs in Skills, lessons, or LLM-generated proposals,
  never in platform, adapters, or a nearby runtime workaround.

## Agent Tool Contract

CareerEng has two kinds of agent-visible capability.

### 1. CareerEng Control and State Tools

Examples: `update_jobs`, `record_application_reviews`, `request_context`,
`update_phase_memory`, `cache_lookup`, `cache_read`, `cache_propose`,
`cache_validate`, `phase_result`, batch/session resume operations.

```text
orchestration/agent_protocol/
  Declares names, input/output schemas, statuses, shared LLM contracts, and
  cross-agent message contracts.

orchestration/commands/
  Receives a validated tool call and routes it to the owning capability.

career/ | evolution/ | platform/
  Implements the actual domain or infrastructure operation.
```

The protocol layer declares tools but never implements business behavior.
Command dispatch never invents business policy; it delegates to the owning
module. Provider, MCP, CLI, and external-agent adapters all consume the same
declarations and command path.

State-recording tools return generic, decision-ready evidence about the data
they persisted without choosing the next workflow action. In particular,
`record_application_reviews` reports whether the current call matched prior
terminal review history, observed changed statuses, or received missing status
details. Skills and the active agent use that evidence to decide pagination;
Python does not encode a site's stopping policy.

### 2. Raw Web Capabilities

Examples: snapshot, click, type, upload, navigation, browser inspection, and
an explicit `browser_sequence` of agent-supplied raw browser calls.

```text
platform/web_control/
  Owns runtime/profile lifecycle, browser-MCP discovery, and raw invocation.
```

Raw browser schemas are discovered from the connected browser MCP where
possible. Do not hand-copy each browser tool schema into provider or Codex
adapters. Raw web control never decides what a site action means.

`browser_sequence` is declared in `orchestration/agent_protocol/` and executed
by `platform/web_control/`. It runs only the ordered steps supplied by the
agent, stops on the first technical error, and returns raw results. It must not
infer page stability, required fields, job policy, or a site-specific flow.

### External-Agent Phase Context Delivery

Provider execution receives the current phase context directly in its request.
External agents may query assembled context through the CareerEng MCP response
when a batch starts, a phase advances, or the agent queries the active batch.
Bounded worker threads use the narrower work-item protocol instead: they start
with only a durable `work_item_id`, fetch a scope and context catalog through
`careereng_get_work_item_context`, then explicitly read only required
resources through `careereng_read_work_item_resource`. The catalog can expose
the current phase's project/site Skill slice, continuation, phase memory,
local state, compatible cache candidates, and state-tool schemas without
eagerly placing them in a worker's first prompt. During apply it can also
describe `apply_facts`, `full_cv`, `full_persona`, and the site-only batch
history view. Those bodies are resolved only after the worker requests them;
the resolver is shared by provider and Codex paths and caches only within the
active runtime scope.

The first work order for a site batch snapshots the project and site Skill
text used to assemble phase context. Later phase and apply-target refreshes in
that same batch derive their slices from the snapshot rather than rereading a
possibly edited Skill. Profile, CV, history, and other user data remain lazy
live resources. An explicit new batch receives a new Skill snapshot.

The initial `apply` envelope is also backend-neutral. It contains only staged
resume path/basename, lightweight form facts, and target identifiers. Full CV,
persona, and site history remain explicit lazy resources. Browser executors
persist an action checkpoint containing only tool name, trace reference, URL,
and technical result; the LLM decides whether it needs another observation or
recovery step.

`workspace/agent_bridge/.../payload.json` and `work_order.md` remain durable
recovery and audit artifacts. They are not the normal, file-reading-only
delivery mechanism for an external agent.

## Native Worker Lifecycle

Desktop execution uses visible, independently openable tasks created through
Desktop `create_thread`, not hidden `spawn_agent` children. The MCP launch spec
declares this presentation/transport contract. The main task executes the
returned plan and registers its visible task ID; CareerEng itself does not call
Desktop tools. Task visibility must be verified by the main task, not inferred
from an arbitrary registered ID. Host tool restrictions take precedence: report
an unavailable operation instead of silently substituting another transport.

Native liveness is derived from valid scoped activity, explicit heartbeats and
bounded in-flight operations. Host execution records activity automatically
after scope validation, and records progress separately when durable state or
context revision advances. Activity timestamps never decide job fit, retries,
or business outcomes. Existing browser progress guards remain responsible for
repetitive no-progress workflow evidence.

Silence beyond the recovery idle limit starts spaced read-only probes. Unserved
probes do not consume additional failures. Only repeated acknowledged checks
without fresh evidence can schedule recovery. Work waiting for the user, paused,
or terminal is not treated as a running silent worker. Recovery and probe actions
carry an activity revision as well as their existing epoch and binding fences;
new activity invalidates stale actions. `careereng_prepare_worker_action`
revalidates immediately before Desktop execution. The Desktop operation is an
external side effect, so no cross-process atomicity is promised for the tiny
post-validation interval; existing work-item leases and terminal guards remain
mandatory. No background daemon may invent worker heartbeats.
The host protocol revision is `2026-09-12.1`: reload MCP and Runtime Host
together before live validation so a new monitor cannot silently use an old
host without activity instrumentation.

When `browser.execution_mode = "native_agent"`, the Codex Desktop App Server
owns live agent execution while CareerEng owns durable orchestration state:

```text
CareerEng batch/site work item
  -> assembled phase context + durable work-order audit artifact
  -> CareerEng publishes a launch specification for the main Agent
  -> the main Agent creates a flat native worker and registers its agent_id
  -> the worker receives a work_item_id and pulls scoped MCP context
  -> the worker uses CareerEng MCP/browser/state tools and reports observed state
  -> CareerEng reconciles desired and observed state into durable action plans
  -> the main Agent executes native spawn/send/interrupt/resume/close actions
  -> the main Agent records action receipts and consumes durable attention events
```

One `site + batch` has one active work item at a time. A persisted
`SiteWorkerSession` remains the bounded business-run continuity generation,
not a live Codex transport owner. It records the active native `agent_id` for
that generation so consecutive batches can route new work items to the same
flat child task. The native worker registry still owns each work-item binding
and its desired/observed runtime state.

Cross-batch continuity is decided mechanically from those two stores. An
active bound child receives a durable `send` action for the new work item; a
suspended child receives `resume` followed by `send`; a missing, terminal,
faulted, or quarantined child produces a replacement `spawn`. The Desktop main
Agent alone executes those actions. CareerEng never calls native task tools or
infers whether a Codex task still exists.

Each batch keeps independent history, report, metrics, evidence, and
checkpoints even when its site work item reuses a child task. Cancellation does
not consume the configured effective-run boundary. At that boundary the
session becomes `review_pending`, receives no further business work, and
CareerEng creates an evolution work item. The main Agent launches that item as
another flat sibling worker; after review, the next site batch starts a new
session generation and native child task. The site worker does not create its
successor or the evolution worker.

`orchestration/engine/site_work_items.py` owns generic queue and slot semantics.
`orchestration/worker_control/` owns native worker bindings, desired/observed
state, reconciliation, action plans, and receipts. `platform/sessions/` keeps
durable business continuity and browser-resource records. There is no
`adapters/codex/` transport and no CareerEng-owned Codex thread coordinator.

`orchestration/worker_control/` owns backend-neutral asynchronous control
contracts. Every executable work item carries a `control_epoch` lease and a
monotonic `site_revision`. Every mutating browser/state call also carries the
worker-observed `context_revision`; an apply terminal result additionally
carries the exact active target job id. MCP validates these values before
forwarding, and the runtime host validates them again immediately before side
effects. Pause, stop, cancel, release, phase refresh, and target refresh
therefore reject stale calls instead of rebinding them to newer site state.
Control states are monotonic, so a delayed interrupt acknowledgement cannot
reopen a cancelled or released item.

Worker commands and worker actions are separate durable layers. A command is
the caller's ordered intent (`guidance`, `redirect`, `resume`, `pause`,
`cancel`, or `recovery`) and remains valid independently of Desktop transport
availability. The command arbiter evaluates only generic lifecycle facts and
turn-boundary safety. Its result is materialized as one or more native actions
(`spawn`, `send`, `interrupt`, `resume`, or `close`) for the main Agent to
execute and acknowledge. Removing an App Server adapter must never remove the
command inbox, arbitration, continuity, or recovery contracts.

Pause is an acknowledged transition: desired state changes first, the
reconciler emits an idempotent interrupt action, the main Agent executes it,
and the worker's observed state confirms suspension. A missing confirmation is
represented as durable uncertainty rather than guessed completion. A later
resume may continue the native worker when possible or launch a replacement
from the same durable work item. The epoch fence, not heartbeat timing,
prevents stale side effects.

Ordinary Skill phases are logical state boundaries, not worker-lifecycle
boundaries. `phase_result(done)` advances durable context synchronously; the
same Codex turn may immediately fetch that context and continue through the
retained browser. The temporary `transitioning` state is used only while the
career-domain continuation prepares another sequence, such as retrieval to
apply. That continuation atomically reopens the same work item as `active`
with higher context and site revisions before the state-tool call returns. A
phase boundary never closes the worker thread or browser runtime. If domain
continuation rejects a completion after the state tool entered `transitioning`,
the host restores the same work item to `active`; it never leaves a live target
stranded between states.

If a native worker ends while its work item is still active, CareerEng records
the observed mismatch and emits a recovery action instead of pretending the
site completed. Exhausted recovery parks the same durable work item in
`waiting_user`, releases its scheduler slot, and leaves the current phase and
item unchanged. A user continuation reissues that item with a new control
epoch; a stale worker cannot regain access after recovery, phase refresh,
pause, cancellation, or release because each accepted state or context change
advances the site revision.

Main-Agent communication is pull-based and durable. Workers publish events to
the CareerEng inbox; the main Agent consumes them through a bounded cancellable
wait tool. A new user message may cancel the current wait without losing an
event. CareerEng does not write directly into an active Desktop turn and does
not depend on a callback into a separately launched App Server.

Phase recovery is monotonic. Once a phase completes, its durable output is the
frozen input for later phases in that batch and recovery never reruns it. A
retrieval interruption resumes its saved page/checkpoint with normal dedupe. An
apply interruption resumes the persisted Apply List and its active target. If
the result of that target is uncertain, the Skill-guided worker reopens that
target's Job URL and reconciles the live page instead of returning to search or
application-history review. Newly posted or removed jobs are deferred to the
next batch.

`agent.site_parallelism` limits active site workers for both Codex and provider
execution. A batch is an aggregation, report, and evidence container, not a
global browser lock.

`platform/runtime_host/` serializes raw browser/state and lifecycle operations
per site only. Site-scoped pause, stop, and cancel never release another site's
worker or runtime and never convert the shared batch into a global stop.
It must never serialize unrelated sites through a workspace-wide runtime lock.
Waiting-user, approval, cancellation, and release events are scoped to the
owning site work item and Codex thread. Provider execution uses the same
batch/evidence/proposal/apply continuation, but has no retained remote thread.

The work order files remain audit and recovery artifacts, not worker startup
instructions. A worker must not scan project files to reconstruct scope. After
it records a phase result that advances the work item, it refreshes the same
work-item context and continues on its existing Codex thread. A user-blocked
phase preserves that thread, retained browser, and batch-scoped history view;
an execution idle timeout only requests a fresh scoped context and snapshot on
that same thread. It does not create a new worker, decide a browser action, or
write a job outcome. Final site completion first marks the work item
`completed`, then releases only that site's worker and browser resources
without clearing durable cache artifacts. Runtime records
only lifecycle, resource-read, tool, cache, and token-usage facts; it does not
choose context resources or workflow strategy for the worker.

A browser process is replaceable infrastructure, not durable workflow state.
When recovery is exhausted because the scoped browser is dead, the runtime host
may discard and recreate only that site's browser process/profile lease while
retaining its payload, phase session, Skill snapshot, Apply List, active target,
work-item identity, and batch. Normal `release_site` remains terminal for that
execution scope and is never used as a recovery shortcut.

User continuation follows a resume-or-recover rule. If the original batch and
work item remain resumable, the host reissues them in place. If the source batch
is terminal or otherwise cannot be reissued, `checkpoint_recovery` creates one
new batch from its last durable result. The restart phase is derived from phase
outputs and the unfinished item, not from a fixed recovery phase. The recovery
batch clones frozen run rows, Apply List, current target, and the exact resume
snapshot version; records source-batch/plan lineage; and excludes browser PIDs,
thread turns, payload bindings, leases, and other transient ownership. Repeated
delivery of the same recovery command returns the same recovery batch.

## Adding a New Tool

Before adding a tool, decide whether it must be agent-visible. Internal helper
functions do not need a tool declaration.

For a new CareerEng tool:

1. Add its contract in `orchestration/agent_protocol/`.
2. Register one generic dispatcher in `orchestration/commands/`.
3. Implement or reuse the owning capability in `career/`, `evolution/`, or
   `platform/`.
4. Use the shared registry so every adapter exposes the same tool.
5. Add contract and execution tests without duplicating adapter-specific logic.

For a raw browser capability, add it to or expose it through
`platform/web_control/`; do not duplicate its declaration in every adapter.

## Change Checklist

Before editing, identify the change type:

- Site behavior or matching/form policy: Skill, lesson, or LLM proposal.
- Career business capability: `career/`.
- Evolution evidence, proposal, validation, or patch mechanics: `evolution/`.
- Generic phase/item progression: `orchestration/`.
- Workspace storage, browser runtime, sessions, metrics, or repair: `platform/`.
- External API, MCP, CLI, Codex, or Claude protocol conversion: `adapters/`.

Then inspect only this document, the owning module, its direct call path, and
the relevant workspace evidence. Do not scan or modify unrelated packages by
default.

If a change alters a boundary, dependency direction, tool contract, or
workspace ownership rule, update this document and the active taskboard before
implementing it.
