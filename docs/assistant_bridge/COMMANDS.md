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

## Safety

Prefer `assistant ingest` first when the user uses natural language. It records the event, classifies the request, and returns the suggested command.

Only execute the suggested command when the user clearly requested execution or confirms the assistant suggestion.

`site bootstrap` prepares the local handoff only: site registry, testable site AI Skill with apply disabled, action card, and evidence pack. It does not start browser phases or enable apply.
