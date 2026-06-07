# Evolution Proposal Schema

An evolution proposal converts an archived evolution run into concrete, rollbackable changes.

The proposal is stored at:

```text
workspace/evolution/runs/<run_id>/proposals/proposal.json
```

The first version does not generate proposals automatically. A proposal may be written by Codex, another assistant, or a future LLM proposal command.

## Required Top-Level Fields

```json
{
  "run_id": "evo_run_xxx",
  "candidate_id": "application_strategy_evolution",
  "diagnosis": "What the evidence suggests.",
  "proposed_changes": [],
  "evaluation_plan": [],
  "risk_notes": []
}
```

Required:

- `run_id`
- `candidate_id`
- `diagnosis`
- `proposed_changes`

Optional:

- `memory_outputs`
- `routing_examples`
- `evaluation_plan`
- `risk_notes`

## Supported Change Types

V1 supports only rollbackable local changes:

- `skill_patch`
- `routing_example_append`
- `memory_unit_append`
- `assistant_context_update`

V1 rejects:

- Python code patch
- config patch
- provider/MCP/browser protocol changes
- login/security/MFA/CAPTCHA behavior changes
- core storage schema migration
- final-submit permission policy changes
- arbitrary shell commands

## skill_patch

V1 supports Markdown section replacement only.

```json
{
  "change_id": "change_1",
  "change_type": "skill_patch",
  "target_file": "skills/search/jobs/sites/qualcomm/SKILL.md",
  "target_section": "Application Status Review",
  "heading_level": 2,
  "patch_strategy": "replace_section",
  "summary": "Clarify dashboard review checklist behavior.",
  "replacement_markdown": "## Application Status Review\n\n...",
  "expected_evaluator_changes": ["lower_no_progress_guard_count"],
  "rollback_required": true,
  "risk": "medium"
}
```

Rules:

- `target_file` must be a Markdown file inside the project root.
- `patch_strategy` must be `replace_section`.
- `target_section` must exist.
- `replacement_markdown` must include the same heading.
- Apply must snapshot the original file first.

## routing_example_append

Append one routing example to:

```text
workspace/assistant_bridge/routing_examples.jsonl
```

```json
{
  "change_id": "change_2",
  "change_type": "routing_example_append",
  "summary": "Add positive implicit career intent example.",
  "row": {
    "text": "我想准备 OpenAI AI infra，需要补什么？",
    "expected_category": "career_intent_strategy",
    "expected_action": "",
    "label_source": "evolution",
    "is_positive": true,
    "confidence": 0.85,
    "semantic_labels": ["career_strategy", "ai_infra"],
    "detected_entities": {}
  }
}
```

The apply layer adds missing IDs and timestamps when needed.

## memory_unit_append

Append one memory unit to:

```text
workspace/evolution/memory/units.jsonl
```

```json
{
  "change_id": "change_3",
  "change_type": "memory_unit_append",
  "summary": "Record AI infra CUDA gap.",
  "row": {
    "memory_type": "career_intent_strategy",
    "status": "candidate",
    "summary": "User is targeting AI infrastructure roles and treats CUDA/GPU systems as a gap.",
    "content": "Evidence-backed memory content.",
    "entities": {"target_domain": "AI infrastructure"},
    "labels": ["ai_infra", "cuda_gap"],
    "source_refs": [],
    "confidence": 0.8
  }
}
```

The apply layer adds missing IDs and timestamps when needed.

## assistant_context_update

Replace the assistant-readable Codex context overlay:

```text
docs/assistant_bridge/CODEX_CONTEXT.md
```

```json
{
  "change_id": "change_4",
  "change_type": "assistant_context_update",
  "summary": "Clarify current memory-intake confirmation policy for Codex.",
  "target_file": "docs/assistant_bridge/CODEX_CONTEXT.md",
  "content_markdown": "# CareerEng Codex Context\n\n..."
}
```

Rules:

- `target_file` must be exactly `docs/assistant_bridge/CODEX_CONTEXT.md`.
- The change replaces the whole file.
- Apply snapshots the previous file and records a diff.
- Use this for concise assistant-facing lessons, not durable user facts.
- Do not use this to bypass `ASSISTANT_GUIDE.md` or `AGENTS.md`.

## Archive Outputs

After apply, the run directory should contain:

```text
applied_patch.diff
applied_files.json
snapshots/before/<relative_path>
```

`run.json` must move to:

```text
status = applied
```

The next stage is evaluation and selection.
