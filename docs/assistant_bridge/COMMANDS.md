# Assistant Bridge Commands

Use these commands through the assistant bridge or directly in a local shell.

## Bridge

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "@career 检查投递状态"
python -m careereng assistant state --client codex --thread <thread_id>
python -m careereng assistant end --client codex --thread <thread_id>
```

## Career Memory

```bash
python -m careereng career-memory promote
python -m careereng career-memory import-candidates /path/to/memory_candidates.jsonl
python -m careereng career-memory list
python -m careereng career-memory show <memory_id>
```

## Action Cards

```bash
python -m careereng action-card list
python -m careereng action-card show <card_id>
python -m careereng action-card close <card_id> --result "reviewed"
python -m careereng action-card cancel <card_id> --reason "not needed"
```

## CareerEng Operations

```bash
python -m careereng jobs review-status
python -m careereng jobs apply
python -m careereng application-summary build
python -m careereng application-summary repair-history
python -m careereng metrics summary
python -m careereng interview create --company unknown --title unknown --created-reason ad_hoc_assist
python -m careereng interview update <session_id> --company OpenAI --title "AI Infra"
python -m careereng interview candidates --company NVIDIA --title SDET
python -m careereng interview create-from-candidate --candidate-id <id>
python -m careereng interview audio-devices
python -m careereng interview capture-audio <session_id> --device 4
python -m careereng site list
python -m careereng site bootstrap "Apple" --url https://jobs.apple.com
python -m careereng site activate <site>
python -m careereng site deactivate <site>
python -m careereng batch-stop
python -m careereng profile generate
```

## Runtime Host

```bash
careereng runtime-host status
careereng runtime-host serve
careereng runtime-host stop
careereng runtime-host release-site --site <site_key>
careereng runtime-host cancel-batch --batch <batch_id>
```

Use one runtime host for the workspace. Do not start a separate host for every site. Releasing or cancelling one site/batch must not stop unrelated active sites.

## MCP Site Execution

MCP exposes a small fixed control surface:

- `careereng_runtime_host_status` and `careereng_get_context` inspect runtime and batch state.
- `careereng_get_work_item_context` establishes the active site task scope.
- `careereng_work_item_list_browser_tools` and `careereng_work_item_list_state_tools` discover currently permitted tools.
- `careereng_work_item_call_browser_tool`, `careereng_work_item_run_browser_sequence`, and `careereng_work_item_call_state_tool` execute within that scope.
- `careereng_work_item_phase_result` closes one phase; `careereng_complete_evolution_solution` continues an applied exploration proposal.
- `careereng_pause_site`, `careereng_stop_site`, and `careereng_cancel_site` change only the named site's lifecycle within its batch.
- `careereng_pause_jobs_batch` and `careereng_cancel_jobs_batch` remain explicit whole-batch controls.

Do not use a static browser-tool list. The current work item determines which browser and state tools are available.
Do not emulate a site-only action by cancelling the batch. A revoked work-item
epoch rejects delayed browser/state calls, while a stale site revision rejects
late lifecycle results.

## Safety

Prefer `assistant ingest` first when the user uses natural language. It records the event, classifies the request, and returns the suggested command.

Only execute the suggested command when the user clearly requested execution or confirms the assistant suggestion.

`site bootstrap` prepares the local handoff only: site registry, testable site AI Skill with apply disabled, action card, and evidence pack. It does not start browser phases or enable apply.
