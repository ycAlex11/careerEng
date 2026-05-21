# Assistant Bridge Commands

Use these commands through the assistant bridge or directly in a local shell.

## Bridge

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "@career 检查投递状态"
python -m careereng assistant state --client codex --thread <thread_id>
python -m careereng assistant end --client codex --thread <thread_id>
```

## CareerEng Operations

```bash
python -m careereng jobs review-status
python -m careereng jobs apply
python -m careereng application-summary build
python -m careereng application-summary repair-history
python -m careereng metrics summary
python -m careereng site list
python -m careereng site activate <site>
python -m careereng site deactivate <site>
python -m careereng batch-stop
python -m careereng profile generate
```

## Safety

Prefer `assistant ingest` first when the user uses natural language. It records the event, classifies the request, and returns the suggested command.

Only execute the suggested command when the user clearly requested execution or confirms the assistant suggestion.

