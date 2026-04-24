---
id: site-microsoft
name: microsoft Site Skill
version: v1
updated_at: '2026-04-15'
scope: site
site_key: microsoft
status: ready
apply_enabled: false
---
# Microsoft Site Skill

## Session Preparation

### Goal

- Complete Microsoft careers login preparation and keep the browser inside the Microsoft careers or apply jobs flow for the next phase.

### Site Facts

- Start from the current Microsoft careers or apply page, not from a generic Microsoft consumer page.
- If the page shows `Sign in`, use the sign-in entry for the current Microsoft careers or application flow.
- The Microsoft auth flow can continue through a provider chooser, a Microsoft-account continuation step, or a remembered-account tile before returning to the jobs system.
- After successful authentication, Microsoft may return to the careers home or apply jobs system instead of opening search results immediately.
- Microsoft careers home can show an account menu before the actual apply/jobs session is fully reusable.

### Completion Or Blocked

- Do not treat the Microsoft careers or apply domain by itself as proof that login completed.
- Do not treat the careers-home header account menu, avatar, `Account manager`, `Saved jobs`, `Settings`, or similar header-only account controls as final proof by themselves.
- Treat those careers-home account controls only as a reusable-session hint. Before ending `Session Preparation`, confirm that the current Microsoft careers or apply flow is still signed in without a visible `Sign in` re-entry on the live working page.
- If the current page is the Microsoft careers public home and it shows a visible jobs entry such as `Find jobs` or the careers jobs `Search` entry, use that entry to move into the real jobs/apply flow before deciding whether the session is ready, as long as the page is not already asking for password entry, verification, MFA, CAPTCHA, email confirmation, or another explicit human-only challenge.
- If moving from careers home into the direct jobs/apply surface reveals `Sign in`, candidate login, create-profile/setup-profile, or another auth continuation, treat authentication as unresolved and continue the Microsoft careers auth flow instead of declaring success.
- If the current live page inside the Microsoft careers or apply working flow shows a stable post-login identity surface and no visible `Sign in` re-entry on that same live page, end `Session Preparation` immediately.
- If the current live page still shows `Sign in`, treat authentication as unresolved and continue the Microsoft careers auth flow instead of declaring success.
- If the flow requires password entry, verification, MFA, CAPTCHA, email confirmation, or another explicit human-only challenge, stop with `blocked`.

### Notes

- Reuse the saved Microsoft browser session in later retrieval and apply runs.

### Don't

- Do not switch into a generic Microsoft homepage, store page, product page, profile page, account page, or broad Microsoft brand navigation during this phase.
- Do not stop this phase only because careers home looks signed in. Microsoft must still stay signed in when the flow advances toward the real jobs/apply surface.
- Do not keep exploring once authentication has already been confirmed on a reusable Microsoft careers or apply working page.

## Channel Discovery

### Navigation

- If Microsoft lands on a dashboard, candidate home, or other logged-in landing page instead of the searchable jobs UI, navigate into the real jobs search surface from that logged-in landing page.
- If Microsoft returns to the careers home page after login and the careers page shows its visible jobs `Search` icon or search button, click that jobs-search entry before ending channel discovery.
- Click that jobs `Search` entry before typing into any search field. Do not use a generic global/header search box as a substitute for opening the real jobs-search surface.
- If Microsoft jobs search opens but the live page still shows a visible `Sign in` entry, candidate-login entry, create-profile/setup-profile prompt, or another auth continuation, treat authentication as unresolved and continue the Microsoft careers sign-in flow before doing discovery or filtering work.
- Do not treat the Microsoft careers home page by itself as channel discovery complete just because the session is authenticated.
- Do not treat a logged-in landing page as discovery-complete while the visible careers search entry still needs to be opened to reach the real jobs search surface.
- If Microsoft has already entered the careers or apply jobs system, stay in that system and continue forward to the searchable jobs surface instead of jumping back to broad Microsoft site navigation.
- Do not click `Settings`, `Action Center`, `Saved jobs`, avatar/account controls, Q&A/community/help links, or other non-jobs navigation while channel discovery is still trying to open the jobs search surface.
- If the current page already shows Microsoft job search controls, filters, or a real jobs list and no visible sign-in re-entry, stop discovery immediately.

### Success Signal

- Treat discovery as complete only after the Microsoft searchable jobs UI or real jobs list is visible.
- The success state for this phase is a page where Microsoft job search can be executed directly without being sent back into sign-in, not merely the authenticated careers landing page.

### Stop Conditions

- Stop if reasonable logged-in navigation still cannot reach the searchable jobs UI.

## Job Filtering

### Filtering Goal

- Stay on the current Microsoft jobs surface and apply the default filtering target from the project jobs skill.
- Prioritize these visible Microsoft filters when available: `Location`, `Profession`, and `Employment type`.
- For Microsoft, the target profession set is broader than only `Software Engineering`. Treat `Software Engineering`, `Applied Sciences`, and `Data Sciences` as valid target profession categories for this phase, plus the closest Microsoft official engineering / applied-ML / data-science category when labels differ slightly.

### Filtering Direction

- Use `Location = China`.
- In `Profession`, if multiple target categories are visible, select all relevant target categories that are shown, especially `Software Engineering`, `Applied Sciences`, and `Data Sciences`, or the closest visible Microsoft official equivalents.
- Do not stop after selecting only one profession if other target categories from that set are also visible in the same Microsoft filter surface.
- Do not return `done` immediately after merely opening `Profession`; finish the concrete profession selection first.
- Use `Employment type = Full-time` when that filter is available.
- Apply only the minimum Microsoft filters needed to reach the project filtering target.
- If Microsoft already shows an active chip, checked option, or URL state for one target dimension, do not reopen that filter.
- Do not open `Work site` in this phase. If Microsoft already exposes a direct remote toggle or current URL state for remote exclusion, reuse that and move on.
- Do not keep toggling `Profession` between multiple unrelated categories once one valid engineering-oriented option has been selected.
- If the current Microsoft results page is already narrowed enough for retrieval, stop and return `done` instead of exploring extra filters.
- After any successful filter application, re-check the current Microsoft jobs surface and return `done` immediately only after `Profession` has been concretely applied and the page is already narrowed to China, engineering / applied-science / data-science-oriented roles, and full-time to the extent the page exposes those filters.
- Do not use `Experience Level`, `Level`, or similar seniority filters in this phase unless the project skill later requires it for a different phase.
- If Microsoft is using an `All filters` dialog, drawer, or modal, finish the concrete selections there and then click its own commit action such as `Show jobs`, `View jobs`, `Apply`, or `Apply filters` before returning `done`.
- If the `All filters` surface is still open, do not return `done`.
- Only finish the phase after the selections are reflected back on the real results surface through active chips, checked states, updated result text, or URL state.

## Job Retrieval

- Use the current live Microsoft results page directly as the source of truth for recording that page.
- After moving to another Microsoft results page, discard assumptions from the previous page and re-read only the current visible page.
- Record the current Microsoft results page before any stop decision.
- Prefer reading the current Microsoft results page as-is before opening any single job detail.
- Treat the current Microsoft search or results-page address as page state only, not as the role link for the visible jobs on that page.
- If the current Microsoft page still leaves some list-level fields or role links unclear, stay on that same results page, do one same-page supplemental read, and then record that page.
- If Microsoft only reveals a missing link or list field after selecting one current-page result card, stay on that same results page, select that current-page result, refresh the current-page read, and continue filling only the still-missing roles for that same page.
- Use a selected-role link only for that matching visible role. Do not reuse one selected-role link for other roles on the page.
- Treat `Apply now` or `/careers/apply?pid=...` as an application-entry link, not as the preferred job-detail URL for the current visible results page.
- Do not treat Microsoft `Similar jobs`, recommendations, or related-role links as members of the current paginated results set.
- Do not let the previous page's selected detail panel, PID, or title stand in for the current page after pagination.
- Do not leave the current Microsoft results page before that page has been recorded.
- Do not move to the next Microsoft results page while the current visible page still has roles with missing concrete role links.
- Continue through the Microsoft results pagination until the final reachable results page has been recorded.
- For Microsoft, move through the visible results pagination sequentially. Do not jump ahead by guessing page URLs, offsets, or hidden pagination targets from the total jobs count.
- On Microsoft, do not declare the current results page finished just because a next-page control is not immediately visible after scrolling. If the live page still shows more jobs than the current recorded page could contain, re-check the real pagination region, results footer, or page label and continue until the final page is explicitly confirmed.
- Do not use time-based early-stop logic on Microsoft search results.
- Microsoft often removes jobs that are no longer open, so keep the jobs returned by the current live Microsoft listing for later filtering and decision-making.

## Apply

### Matching Override

- Use Microsoft's own match signal as the highest-priority application gate after the global job filtering rules have been applied.
- Only move forward with roles labeled `Strong Match` or `Good Match`.
- If a role does not show a clear Microsoft `Strong Match` or `Good Match` signal on the current live page, do not recommend apply for Microsoft. Mark it `filtered_out` and move on.
- Do not fall back to the common matching rule for Microsoft when the site-native match signal is absent, unclear, or weaker than `Good Match`.

### Form Filling

- In the Microsoft `Resume` section, treat the page as satisfied only when it shows the current run's staged resume PDF.
- Compare the current live page's selected Microsoft resume name against the staged resume basename from the apply context.
- If the selected Microsoft resume name already matches that staged basename, treat the `Resume` section as satisfied and continue without uploading again.
- Only if the selected Microsoft resume name is different from the staged basename or the page clearly shows no usable selected resume, click `Upload new`, then upload the staged PDF, then re-read the same page and confirm the staged basename is now selected before moving on.
- For gender questions, answer `Male`.
- For policy, compliance, code-of-conduct, or similar acknowledgement questions, select `Yes`.
- For legal right-to-work or authorization questions, select `Yes`.
- For sponsorship, visa-transfer, or future-visa-requirement questions, select `No`.
- For routine identity, eligibility, and acknowledgement fields, use the fixed rules above and lightweight apply facts without requesting full CV/persona.
- For role-specific experience, qualification, or open-ended questions, call `request_context` for `full_persona` or `full_cv` only when the attached lightweight facts are insufficient.
- If a required question still lacks factual support after requesting the relevant context bundle, stop and ask the user instead of guessing.

### Site Signals

- If a role shows `View application`, treat it as already applied and record that state instead of submitting again.
- Treat a Microsoft application as successfully submitted only after the final `Submit application` or `Submit` action leads to an explicit success confirmation.
- Accept success confirmations such as `application submitted`, `application received`, `submitted successfully`, or an equivalent confirmation page or confirmation state in the Microsoft application flow.

### Escalation

- If the submit action was clicked but no clear success confirmation appears, do not record the job as submitted automatically. Stop and ask the user to verify the result.
- Stop and ask the user to take over before any irreversible submission step if the page content is ambiguous.
- Stop if Microsoft asks for information that cannot be answered from lightweight facts, requested context bundles, or the fixed rules in this skill.
