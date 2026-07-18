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
    project_state/              Taskboard and assistant/project-level state
    maintenance/                Cleanup, repair, diagnostics

  adapters/                     External protocol adapters only
    bootstrap.py                Application composition root for CLI/MCP/host adapters
    providers/                  OpenAI, Anthropic, and other API transports
      browser_phase_runtime.py  Responses API browser-tool execution adapter
    mcp/                        CareerEng MCP server transport
    cli/                        Command-line transport
    host/                       Deprecated compatibility exports for the runtime host
    external_agents/            Codex, Claude Code, and future local-agent adapters
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
  tmp/ debug/
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

## Runtime Session Boundary

`platform/sessions/` owns persistent session messages/state, browser-profile
locks, and generic retained browser runtime lifecycle. Its runtime registry
starts, reuses, releases, and protects a local browser MCP process for a
caller-provided profile; it does not know a site's phase sequence, matching
policy, or browser outcome semantics.

Legacy browser phase runners may retain a thin compatibility method that
supplies the site profile and writes domain session status. They must delegate
runtime ownership to `platform/sessions/` rather than maintain their own
active-process map or profile-lock lifecycle.

## Runtime Host Boundary

`platform/runtime_host/` owns the workspace-scoped local process boundary for
browser/session execution. One host owns the workspace runtime and delegates
per-site browser/profile access to `platform/sessions/` and
`platform/web_control/`; it does not own site policy or create one process per
site.

Its versioned protocol is intentionally generic: `ping`, batch/resume/pause,
and browser/state tool transport. Every response includes a protocol version.
MCP and external-agent adapters connect to an already user-owned host and must
not start a browser-owning process from a constrained desktop sandbox. A
missing or stale host is a recoverable infrastructure condition, reported as
`runtime_host_unavailable` or `runtime_host_protocol_mismatch`, never as a job
or site failure.

CLI may explicitly run the lifecycle commands:

```text
python -m careereng runtime-host serve
python -m careereng runtime-host status
python -m careereng runtime-host stop
```

The old `adapters/host/workspace_manager.py` and hidden `manager-serve` command
are compatibility shims only. Do not add new behavior there.

## Persistence Access Boundary

Generic JSONL and versioned-document primitives are imported directly from
`platform/persistence/`. Legacy `storage/jsonl.py` and `storage/domain_store.py`
remain compatibility exports only. Domain stores still own their workspace
schemas, paths, and semantic state transitions; platform persistence must not
centralize those decisions.

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
`update_phase_memory`, `phase_result`, batch/session resume operations.

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

Examples: snapshot, click, type, upload, navigation, and browser inspection.

```text
platform/web_control/
  Owns runtime/profile lifecycle, browser-MCP discovery, and raw invocation.
```

Raw browser schemas are discovered from the connected browser MCP where
possible. Do not hand-copy each browser tool schema into provider or Codex
adapters. Raw web control never decides what a site action means.

### External-Agent Phase Context Delivery

Provider execution receives the current phase context directly in its request.
External agents receive the equivalent assembled context through the CareerEng
MCP response when a batch starts, a phase advances, or the agent queries the
active batch. The context contains the current phase's project/site Skill
slice, continuation, phase memory, local state, and state-tool schemas.

`workspace/agent_bridge/.../payload.json` and `work_order.md` remain durable
recovery and audit artifacts. They are not the normal, file-reading-only
delivery mechanism for an external agent.

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
