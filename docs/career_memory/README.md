# Career Memory

Career Memory is the local long-term memory layer for job-search conversations.

It is separate from integrations:

- `careereng/integrations/assistant_bridge/` receives assistant messages, classifies them, manages thread scope, and stores raw intake events.
- `careereng/career_memory/` promotes assistant signals or Codex-curated batches into unified memory units.
- `careereng/evolution/` consumes memory units later when improving routing, resume/profile suggestions, company intelligence, or application strategy.

## Storage

Unified memory units are stored at:

```text
workspace/memory/memory_units.jsonl
```

Existing raw signal files stay intact:

```text
workspace/assistant_bridge/intake_events.jsonl
workspace/memory/profile_signals.jsonl
workspace/memory/intent_signals.jsonl
workspace/memory/application_feedback_signals.jsonl
workspace/interviews/events.jsonl
workspace/assistant_bridge/correction_events.jsonl
```

## Memory Unit Shape

Each memory unit keeps a small, flexible schema:

- `memory_id`
- `created_at`
- `updated_at`
- `category`
- `source_event_id`
- `source_signal_id`
- `source_text`
- `summary`
- `facts`
- `entities`
- `confidence`
- `status`
- `tags`
- `supersedes`
- `evidence_refs`

The top-level categories stay stable and reuse the assistant bridge categories:

- `profile_resume_signal`
- `career_intent_strategy`
- `application_feedback`
- `interview_record`
- `correction`

Do not create a new top-level category for every business nuance. Put semantic detail in `facts`, `entities`, and `tags`.

## Single-Message Flow

Use assistant bridge intake first:

```bash
python -m careereng assistant ingest --client codex --thread <thread_id> -m "@career 我想投 AI infra，需要补什么？"
```

Then promote raw signals into memory:

```bash
python -m careereng career-memory promote
```

## Codex Batch Curation Flow

When Codex can see a long current thread, it can curate recent relevant messages into JSON or JSONL memory candidates, then import them:

```bash
python -m careereng career-memory import-candidates /path/to/memory_candidates.jsonl
```

Candidate fields:

- `category`
- `summary`
- `facts`
- `entities`
- `tags`
- `evidence_text`
- `confidence`
- `source_thread_id`

Python validates, deduplicates, and stores the candidates. Codex remains responsible for understanding the thread context.

## Inspecting Memory

```bash
python -m careereng career-memory list
python -m careereng career-memory show <memory_id>
```
