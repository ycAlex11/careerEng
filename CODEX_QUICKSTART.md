# Codex Quick Start

This guide is for a new user who wants to operate CareerEng through Codex.

Read this as a user-facing checklist. Codex can also follow the commands and checks directly.

## 1. Open The Repo In Codex

Open the CareerEng repository root in Codex.

Before asking Codex to run browser automation, run this once in your normal terminal:

```bash
sudo chown -R $(id -u):$(id -g) ~/.npm
```

This avoids common npm permission failures when Codex starts Playwright MCP / browser tooling.

## 2. Install And Initialize

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
careereng onboard
```

Then add provider keys to:

```text
auth.json
```

For visible browser automation, keep Chrome non-headless in:

```text
config.toml
```

Useful browser settings:

```toml
[browser]
headless = false
browser_name = "chrome"
site_parallelism = 1
```

## 3. Put Your Markdown Resume In The Workspace

CareerEng expects the current Markdown resume under:

```text
workspace/cv/current/
```

Recommended command:

```bash
careereng resume upload --file ./resume.md
```

Then export the apply-ready PDF:

```bash
careereng resume export-pdf --file ./resume.md
```

The apply-ready PDF should be the only PDF under:

```text
workspace/cv/exports/
```

If there are multiple PDFs there, site apply flows may treat the resume as ambiguous.
By default, the PDF filename includes a timestamp and content hash. Keep that default unless you have a specific reason to force a fixed filename; changing filenames helps career sites distinguish newer resume versions.

## 4. Let Codex Inspect Readiness

Send this in Codex:

```text
@career 检查项目是否初始化完成
```

Codex should inspect:

- `config.toml`
- `auth.json`
- `workspace/`
- `workspace/cv/current/`
- `workspace/cv/exports/`
- `workspace/profile/`
- `workspace/sites/`

## 5. Generate Profile And Start The Job Search

Send these in Codex:

```text
@career 帮我根据简历生成用户画像
```

```text
@career 找适合我的外企软件工程公司，给出 top 10
```

After CareerEng returns company candidates, tell Codex which ones to register or activate.

Examples:

```text
@career 注册 Microsoft 和 NVIDIA
```

For first-time setup or debugging, activate only one site at a time. This keeps browser behavior easier to inspect and avoids multiple career sites failing at once.

```text
@career 激活 Microsoft 和 NVIDIA
```

Better first run:

```text
@career 只激活 Microsoft
```

## 6. Run Status Review Or Apply

To check existing applications:

```text
@career 检查投递状态
```

To retrieve and apply for active registered sites:

```text
@career 检索投递已激活的网站
```

Human takeover is still required for passwords, MFA, CAPTCHA, verification codes, and required questions that cannot be answered from local profile/resume/Skills.

## 7. Save Useful Codex Conversation Back Into CareerEng

When a Codex discussion contains useful career, resume, application, interview, or evolution knowledge, ask Codex to summarize a dynamic number of recent messages:

```text
@career 总结最近 100 条对话并沉淀到本地
```

Codex should create a memory candidate JSONL file and import it with:

```bash
python -m careereng assistant import-candidates <candidate_file> \
  --source-client codex \
  --source-thread <thread_id> \
  --source-limit 100
```

Use the number requested by the user. For example, if the user says "最近 30 条", use `--source-limit 30`.

## 8. Stop A Stuck Batch

If a browser batch is stuck or Codex was interrupted:

```bash
python -m careereng batch-stop
```

This is preferred over manually killing random Chrome or manager processes.

## 9. Core Rule

Use Codex as the operator, but keep CareerEng as the source of truth.

Codex can inspect files, run commands, draft Skills, summarize evidence, and propose improvements. CareerEng owns local state, reports, job history, memory, metrics, and evolution evidence.
