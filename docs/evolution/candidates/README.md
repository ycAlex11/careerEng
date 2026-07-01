# Evolution Candidate Specs

Evolution candidates define what CareerEng is allowed to improve, what evidence is required, and how improvement should be evaluated.

They are the framework-level equivalent of AlphaEvolve's candidate programs plus evaluators, adapted to CareerEng's job-search domain.

Read `docs/evolution/EVOLUTION_STRATEGY_ROUTER.md` before choosing a candidate. The router selects the strategy family; each candidate spec defines the concrete loop, evidence policy, proposal types, validation, and persistence rules for that strategy.

## Purpose

A candidate spec should answer:

- What can be improved?
- What evidence should be collected?
- What proposals may the LLM generate?
- How should the proposal be evaluated?
- What should be archived?
- What is the safety and apply policy?

The goal is to let new evolution behavior be added by writing a spec, not by adding a new hard-coded Python branch for every case.

## Spec Contract

Every candidate spec should define YAML front matter:

- `id`: stable candidate identifier.
- `name`: human-readable name.
- `target_type`: kind of target, such as `project_skill_section`, `site_skill_section`, `assistant_router`, `memory_unit`, `report_summary`, or `data_repair_plan`.
- `target_ref`: file, section, command, or logical object being improved.
- `risk_level`: low, medium, or high.
- `apply_policy`: whether proposals may be auto-applied, auto-drafted, or require human approval.

The Markdown body should define:

- `purpose`: why this candidate exists.
- `required_evidence`: local data needed before a useful evolution run can happen.
- `evolution_strategy`: loop shape, Codex intervention points, and when run-local versus durable change is expected.
- `evidence_selection_policy`: which evidence sources Codex should consider and how it should choose them.
- `allowed_proposals`: proposal types the LLM may generate.
- `evaluators`: deterministic, LLM-assisted, or human evaluation signals.
- `archive_requirements`: what must be saved for later selection, rollback, or comparison.
- `output_contract`: expected proposal outputs.

## Current V1 Candidates

| Candidate | Purpose | Risk |
| --- | --- | --- |
| `assistant_router_memory_intake` | Improve when Codex/assistant conversations enter CareerEng memory. | Medium |
| `application_strategy_evolution` | Improve application strategy from outcomes, JD signals, and user gaps. | High |
| `new_site_workflow_transfer` | Draft a new company's site AI Skill from existing site workflow patterns. | Medium |
| `resume_profile_evolution` | Draft evidence-backed resume/persona improvements and synchronize accepted CV Markdown changes to PDF. | High |
| `site_workflow_compaction` | Compact repeated site operations into reusable skill/workflow patterns. | Medium |
| `target_company_intelligence_evolution` | Build target-company role intelligence, user gaps, and preparation plans from local evidence. | High |

`new_site_workflow_transfer`, `apply_form_workflow`, and `site_workflow_compaction` are the `site_workflow_evolution` family. Keep them as separate specs, but let Codex read them together when a site-workflow proposal needs bootstrap, apply-form, and compaction context.

## Adding A New Candidate

Add a new Markdown file under this directory.

The new candidate should keep business interpretation in Markdown and LLM prompts, while Python remains responsible for:

- loading specs
- collecting evidence
- building context packs
- running deterministic evaluators
- archiving runs and evaluations
- enforcing safety boundaries

Python may provide an evidence index and starter excerpts. Codex/LLM chooses which indexed evidence to inspect and which strategy to propose.

Do not add a new candidate if it belongs to infrastructure or security correctness rather than experience-driven evolution. Provider transport, MCP/browser protocol, login security, MFA, CAPTCHA, final-submit permission policy, and core storage schema are outside the evolution scope.
