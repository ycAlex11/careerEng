# CareerEng Architecture Contract

## Purpose

This document is the architectural entry point for humans and AI assistants.
Read it before changing module boundaries, tools, adapters, workspace state,
browser control, evolution, or workflow execution.

It defines the target architecture. Existing mixed modules are compatibility
code during migration, not a reason to extend the old boundaries.

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
    codex/                      Codex App Server transport, thread bindings, worker lifecycle
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

`careereng_list_agent_events` and `careereng_ack_agent_events` are polling
tools for this inbox. `careereng_get_agent_status` is separate: it reads the
host's current per-site worker/browser state and answers what is running now.
It is not a batch projection and it does not replace durable events.

For immediate Codex delivery, `careereng_register_main_agent` stores the
current App Server thread id at `workspace/agent_events/main_agent.json`. The
Codex-specific `adapters/codex/main_agent_bridge.py` subscribes to the shared
dispatcher, then delivers durable `action_required`, `review_required`, phase
advance, site completion, and batch completion events to that registered
thread. Raw heartbeat and audit events are never delivered there. Delivery
attempts are recorded separately; an App Server failure leaves the event in
the inbox for retry after a new registration or host restart. Replacing the
registered thread id transfers future main-agent notifications to the new
Desktop control conversation.

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

For `codex_app_server`, creating a work order is not sufficient to call a
batch browser-active. The runtime host must successfully start a scoped Codex
worker thread for every active site. A worker starts from its work-item
directory, not the project root; on App Server startup timeout the host drops
that transport and retries with a fresh connection before it marks the worker
unavailable. This keeps startup bounded and prevents a stale App Server from
blocking all sites.

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

## Codex Worker Lifecycle

When `browser.execution_mode = "codex_app_server"`, the Codex App Server owns
the live agent execution lifecycle:

```text
CareerEng batch/site work item
  -> assembled phase context + durable work-order audit artifact
  -> orchestration binds it to its retained SiteWorkerSession
  -> adapters/codex/ starts or resumes that session's Codex thread
  -> Codex thread receives a work_item_id and pulls scoped MCP context
  -> Codex thread uses CareerEng MCP/browser/state tools
  -> Codex App Server emits turn lifecycle events
  -> CareerEng records thread/turn linkage, updates batch evidence, and emits
     a durable main-agent event when user attention or a completion milestone is needed
```

One `site + batch` has one active work item at a time. A persisted
`SiteWorkerSession` may bind consecutive effective work items for the same
site and backend to one external-agent thread. The session is a bounded
continuity layer, not a replacement for batches: each batch keeps independent
history, report, metrics, and evidence. Cancellation does not consume the
configured effective-run boundary. At that boundary CareerEng creates an
evolution review task; it does not automatically make a business decision.
An exploration run creates the same review task immediately after its terminal
evidence is persisted. The applied Codex proposal decides either `ready` or a
bounded follow-up exploration run. A follow-up requeues only that site in the
same batch and retains its Codex thread; the batch itself is never rewritten
to `waiting_solution` just to hold that review task.

`orchestration/engine/site_work_items.py` owns generic queue and slot
semantics, `orchestration/engine/agent_workers.py` owns retained external-agent
thread lifecycle, and `platform/sessions/site_workers.py` persists session and
thread bindings. The same configuration exposes the new-site exploration loop
limits and recurring review cadence. These are structural counts only; Codex
and Skills decide success, continuation, cache value, and proposed evolution.
`adapters/codex/` only translates a claimed item to Codex App Server RPC/events.
A future Claude Code adapter supplies the same thread transport contract rather
than another lifecycle state machine.

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

Pause is an acknowledged transition: `active -> pausing -> paused`. Transport
activity is treated as heartbeat evidence, and the coordinator repeats the
idempotent interrupt probe only within the configured retry bound. If no
terminal turn event arrives, the item becomes `pause_unconfirmed`; its old
thread is quarantined and a later resume starts from durable CareerEng state on
a replacement thread. This recovery mechanism detects transport uncertainty;
the epoch fence, not heartbeat timing, prevents stale side effects.

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

If a Codex turn nevertheless ends while its work item is still `active`, the
coordinator starts a bounded continuation on the retained thread. Repeated
turn endings without a context revision become an execution-recovery failure,
not a completed site and not a permanently false `running` worker. A stale
turn cannot regain access after phase refresh, pause, cancellation, or release
because each accepted state or context change advances the site revision.

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
