---
id: resume-sync
name: Resume Sync Skill
version: v2
owner: careereng
---

# Resume Sync Skill

## Objective

Use resume text to generate high-quality updates for `persona.md`, and produce a conservative candidate update for `intent.md`.

## Scope

- This skill is for resume parsing and structuring.
- Do not run relatedness gating in this flow (resume is inherently profile-related).
- Do not use external search in this version.

## Extraction Order (mandatory)

1. Personal info block
2. Work experience block
3. Education block (may appear at top or bottom)
4. Project block
5. Skills block
6. Final profile summary

## Rules

### 1) Personal information first

- Resume opening lines usually contain personal info.
- Prioritize extracting: `basic.name`, `basic.current_city`, `basic.nationality`, `basic.languages`.
- For Chinese resumes, support labels like `姓名`, `现居地`, `电话`, `邮箱`, `语言`.

### 2) Work duration aggregation

- Parse each company experience entry with start/end time.
- Compute cumulative years of experience from work periods.
- If periods overlap, do not double count overlap duration.
- Save raw role/company items in `experience[]`.

### 3) Role inference from background

- Infer likely target roles using education + work + projects.
- Typical examples: software engineer, backend engineer, data engineer, ai engineer.
- Write inferred roles as candidates for `intent.target_roles` (confirmation required before apply).

### 4) AI experience split

- Distinguish AI experience into two subtypes:
  - Machine Learning
  - Deep Learning
- Put both as normalized entries under `skills.ai` when evidence exists.

### 5) Education placement tolerance

- Education may appear at the beginning or the end.
- Scan full resume before concluding education is missing.
- Extract school/degree/major/year when available.

### 6) Project structure

- Each project should include at least:
  - title/name
  - short description/scope
- Store in `projects[]`.

### 7) Tool/framework mining from projects

- Use project descriptions to infer:
  - `skills.frameworks`
  - `skills.tools`
  - `skills.programming`
- Normalize aliases (e.g., `K8s` -> `Kubernetes`).

### 8) Summary generation

- Build `summary.profile` and `summary.work_style` from education + projects + experience.
- Keep summary factual and concise; avoid marketing language.

## Output Constraints

- Only output keys that exist in `persona.md` / `intent.md` schemas.
- Never fabricate facts that are not supported by resume evidence.
- If uncertain, leave field unchanged or emit low-confidence candidate only.

## Intent Candidate Policy

- `persona.md` patch: may be applied automatically.
- `intent.md` patch: always candidate-only and requires explicit user `y/n` confirmation.
