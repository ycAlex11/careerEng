---
id: search-jobs
name: Search Jobs Skill
version: v1
updated_at: "2026-03-08"
scope: jobs
---

# Search Jobs Skill

## Input Priority

Use the current user request, the workspace user job preference skill if present, the project search skills, and `intent.md` to recommend concrete companies.
When they conflict, use this priority order:
1. current user request
2. workspace user job preference skill
3. project search skills
4. `intent.md`

Treat the workspace user job preference skill as the higher-priority expression of what kind of companies and jobs the user wants now.
Return concrete employer names, not categories.

## Company Recommendation Stage

During company recommendation, focus first on company attributes instead of forcing an exact job title match.
Prioritize companies that match the user's company-level preferences, such as:
- Western foreign employers, especially US or Europe technology companies
- established engineering organizations
- full-time experienced-hire hiring patterns
- onsite or hybrid hiring in China when available

Treat AI-related products, infrastructure, or business lines as a bonus signal at this stage, not a hard requirement.
At the company stage, it is acceptable to keep companies that are a strong organizational match even if their current public role titles are not yet visible.

## Registered Company Channel Discovery

After the user selects companies to register, immediately begin locating where to apply for each selected company.
Use Playwright plus Google search for this step.
Prefer the official company website first, especially official careers or jobs pages.
If the official website is not obvious, search with company name plus likely hiring keywords such as careers, jobs, workday, greenhouse, lever, or LinkedIn jobs.

For each registered company, search until one of these happens:
- a real jobs list, jobs table, job cards, or open-roles listing becomes visible
- a clear application entry page for that company's hiring system is found
- several reasonable search attempts fail

Stop searching that company as soon as a page with a visible job list or reliable application entry has been found.
If no reliable job-posting URL is found after a few reasonable searches, stop and leave that company unresolved.

## Job Retrieval And Selection

Once a company application URL or jobs page is found, inspect the published roles.
Prioritize software engineering, backend, platform, systems, and architecture-adjacent roles first.
Then treat AI-agent, agentic systems, and LLM-application roles as strong bonus matches.
Use `persona.md` only when judging whether concrete jobs are a realistic fit for the user.
When in doubt, prefer technically relevant engineering roles over broad unrelated roles.
