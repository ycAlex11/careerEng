---
id: site-nvidia
name: nvidia Site Skill
version: v1
updated_at: '2026-04-10'
scope: site
site_key: nvidia
status: ready
apply_enabled: false
---
# NVIDIA Site Skill

## Session Preparation

### Goal

- Complete NVIDIA login preparation and leave the browser in the NVIDIA jobs flow so the next phase can continue from the same jobs system.

### Site Facts

- Start from the current NVIDIA jobs entry URL stored in `site.json`.
- NVIDIA's public jobs entry page can already show search, filters, and visible job listings before login. Those elements alone do not prove that authentication is complete.
- NVIDIA authentication for this flow normally continues through Google sign-in.
- After successful sign-in, the flow may briefly land on a candidate or user-home page before returning to the NVIDIA jobs system.

### Completion Or Blocked

- If the current live page shows a signed-in NVIDIA jobs state, such as candidate home, user home, account menu, avatar, welcome banner, profile entry, or another obvious post-login identity surface inside the Workday jobs flow, end `Session Preparation` immediately.
- End `Session Preparation` only when the NVIDIA flow now shows an actual signed-in identity surface or another explicit post-login state inside the jobs system. Returning to the public NVIDIA jobs page by itself does not prove that authentication completed.
- If the current NVIDIA flow has reached a sign-in page but still shows a visible continuation such as Google sign-in, SSO/provider choice, remembered account choice, or another clear next login step, continue through that visible path instead of stopping.
- Stop with `blocked` only when the flow has reached password entry, verification, MFA, CAPTCHA, email confirmation, or another explicit human-only challenge and there is no further visible one-click continuation.

### Notes

- Treat the saved browser profile as the default login state for later retrieval and apply runs.

### Don't

- Do not stop just because the public jobs landing page already shows search, filters, or visible jobs.
- Do not treat the first NVIDIA `/login` page by itself as a blocked state when a visible sign-in continuation is still available.
- Do not keep exploring candidate, profile, account, or unrelated navigation paths after login preparation is already complete.
- Do not start job filtering actions while still in `Session Preparation`.

## Channel Discovery

### Navigation

- Start from the current NVIDIA jobs entry URL.
- If the current page already shows the jobs-system search UI, filters, or visible job list, stop discovery immediately.
- Stay inside the same NVIDIA jobs system page after login; only do extra in-page navigation if the searchable jobs UI still has not appeared.
- Once login returns to the NVIDIA Workday jobs page with a search box, filters, or visible roles, end channel discovery and move straight into job filtering. Do not pause for user confirmation at that point.

### Success Signal

- Treat discovery as complete only after the NVIDIA jobs system shows a real search bar, filters, or visible jobs list.

### Stop Conditions

- Stop if the current NVIDIA jobs entry page still cannot reach the searchable jobs UI after reasonable login and in-page navigation attempts.

## Job Filtering

### Filtering Goal

- Stay on the current NVIDIA jobs page and apply the default filtering target from the project jobs skill.
- NVIDIA usually exposes the needed controls directly on the page through `Location`, `Time Type`, `Job Category`, and sometimes `More`.

### Filtering Direction

- Use `Location = China` when the location filter is available.
- Use `Time Type = Full time`.
- Use the closest visible engineering category in `Job Category`.
- Apply only the minimum NVIDIA filters needed to reach the project filtering target.
- If NVIDIA already shows an active chip, checked option, or visible current-selection state for one target dimension, do not reopen that filter.
- If `More` exposes a remote / work-site style filter, exclude remote-only roles there; otherwise do not keep searching forever for that control.
- If a filter opens a dialog, apply the concrete visible option and then use the dialog's own apply / view-jobs action before moving on.
- Once NVIDIA has applied the main filters and the current jobs list is ready for retrieval, stop and return `done` instead of reopening filters.
- After any successful filter application, re-check the current NVIDIA jobs surface and return `done` immediately if China, full time, and engineering-category narrowing are already in effect to the extent the page exposes them.

## Job Retrieval

- Record the full current NVIDIA results page before any stop decision.
- Do not open a single NVIDIA job detail before the current NVIDIA results page has been recorded.
- Retrieve NVIDIA jobs from the current live listing and keep the retrieved results for later filtering and decision-making.
- If the live NVIDIA page still shows result signals such as page labels, visible job cards, or pagination but the current attempt returns zero jobs, capture a fresh snapshot and retry the same current results page once before stopping or paginating.
- Treat `Posted 30+ Days Ago` as an old-role signal.
- If the current visible NVIDIA page contains any roles marked `Posted 30+ Days Ago`, finish recording that full page and then stop retrieval without opening the next page.

## Apply

### Matching Override

- NVIDIA does not add a stronger fit rule here by default.
- Use the common matching rule from the project jobs skill unless the live page exposes a clearer site-native decision signal for the current role.

### Form Filling

- For the first NVIDIA job you apply in the current batch, follow the normal visible resume upload / autofill path on the live page.
- For later NVIDIA jobs in the same current batch, if the live page offers `Use My Last Application`, prefer that reuse path before falling back to the normal visible application entry.
- Use the default-third-option rule only for the `How Did You Hear About Us?` dropdown when no more specific source choice is required.
- For country, region, residence, nationality, or phone-country-code style selectors, use the factual China value from the current profile context instead of a default ordinal choice.
- For gender questions, answer `Male`.
- For policy, compliance, code-of-conduct, or similar acknowledgement questions, select `Yes`.
- On NVIDIA `Autofill with Resume` or any later `Resume/CV` section, if the staged PDF is not yet visibly accepted on the current page, use the page's upload entry such as `Select files` first, then upload the staged PDF, then re-read the page before continuing.
- If NVIDIA apply returns to `Create Account / Sign In`, prefer the visible Google sign-in continuation when it is available on the live page instead of stopping immediately.
- If NVIDIA apply is back on `Create Account / Sign In` but only password entry, email entry, MFA, verification, CAPTCHA, or another human-only challenge remains, stop that job as `blocked`.
- Do not click `Continue` or `Save and Continue` while the current NVIDIA page is still waiting for the staged PDF to be accepted.
- On NVIDIA `My Experience` and `Education`, clear the autofill-generated `Work Experience` and `Education` entries first.
- Do not add new `Work Experience` or `Education` entries after clearing them.
- If those sections still block progress, keep deleting or clearing the blocking auto-generated entries or section content instead of filling new experience or education fields.
- After the current NVIDIA job is already judged `recommended_apply`, routine NVIDIA form filling should rely on this site skill and lightweight apply facts; do not request full CV/persona just to refill Work Experience or Education.
- Request `full_cv` or `full_persona` only if NVIDIA asks a required role-specific or open-ended question that the fixed rules and lightweight facts cannot answer.
- Continue through the flow step by step whenever the next page is safe and clear, until the final `Submit` step.

### Site Signals

- If a role shows `View Application`, treat it as already applied and record that state instead of submitting again.
- On NVIDIA final review, if the live page clearly shows the current job's review step, no blocking validation errors, and a visible `Submit` button, click `Submit` directly.
- Treat the job as submitted only after NVIDIA shows an explicit final application-success confirmation on the live page.

### Escalation

- Stop and ask the user to take over only if the final review state or submit outcome is ambiguous.
- Stop if NVIDIA asks for information that cannot be answered from lightweight facts, requested context bundles, or the fixed rules in this skill.
