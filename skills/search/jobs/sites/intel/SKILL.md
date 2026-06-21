---
id: site-intel
name: Intel Site Skill
version: v1
updated_at: '2026-06-21'
scope: site
site_key: intel
status: ready
apply_enabled: true
retrieval_policy:
  preferred_sort: newest_first
  posted_window_days: 0
  posted_window_comparison: strictly_less_than
  date_window_stop_enabled: false
  history_fast_stop_enabled: true
  unknown_posted_age: review
apply_candidate_policy:
  posted_window_days: 0
  posted_window_comparison: strictly_less_than
  unknown_posted_age: review
---
# Intel Site Skill

Intel uses an official careers entry that may redirect into an Intel Workday jobs surface.
Treat this as a new Workday-style site: reuse the project jobs policy first, use NVIDIA/Workday lessons when the live page behaves similarly, but do not assume NVIDIA-specific labels unless Intel shows them.

## Site Policy

### Retrieval Policy

- Start from the Intel entry URL in `workspace/sites/intel/site.json`.
- Prefer the official Intel jobs surface and any Intel Workday redirected URL it opens.
- Retrieve the current results page before making any pagination stop decision.
- For the current Intel validation run, do not exclude jobs by posted age. Record visible posted labels and dates, but allow older roles such as `30+ Days Ago` to enter apply review when other rules allow it.
- Do not use posted age as an immediate pagination stop for Intel while this validation override is active.
- If the site is clearly sorted newest-first, still record the current page before any non-date stop decision.
- If the site order is unclear, keep paginating until no real next-page / load-more control exists or a safe project-level history stop condition is met.
- Preserve title, URL, location, posted label/date, employment type, and Intel/Workday job id when visible.

### Application Review Policy

- If signed in and an Intel Workday Candidate Home / My Applications / application dashboard is visible, review it before new job discovery.
- If the browser is on a public Intel jobs page and no signed-in Candidate Home / My Applications dashboard is visible, do not continue review or discovery as if login is ready.
- For this new Intel site, first-run login is a human takeover boundary. Open the visible Intel / Workday sign-in entry if needed, then stop with `blocked` and ask the user to complete login.
- After the user completes login and the saved profile has a signed-in Intel / Workday state, resume from the live page and continue review/discovery.
- For application records, follow the project canonical/raw schema: normalized status in `application_review_status`, exact Intel text in `application_review_status_raw`.
- Intel `Not Submitted`, `Continue Application`, `Resume Application`, `Draft`, or equivalent unfinished application wording maps to the project-level `resumable` review status. Record the exact Intel wording in `application_review_status_raw`.
- Treat active/submitted/in-review/current application lists as realtime areas and review all reachable pages inside the review window.
- Treat inactive/rejected/closed/archived areas as historical areas and stop early only after the current page is recorded and already covered by local terminal history.

## Matching Policy

### Application Gate

- Use the project common matching rule unless Intel exposes a clearer site-native decision signal for the current role.
- Hard-exclude intern, internship, campus, student, graduate, new-grad, co-op, 校招, and 实习 roles before opening any apply flow.
- Do not filter out Intel roles only because they are posted 30 days ago, `30+ Days Ago`, or older while this validation override is active.
- Still record posted age/date evidence for reporting and later policy decisions.
- If Intel shows an already-applied, submitted, application received, or view-application state on the live job page, record that state and move to the next apply target.
- If Intel shows a resumable application state on the dashboard or job page, resume that application directly. Do not re-score the JD/CV before continuing the already-started Intel application.

## Session Preparation

### Authentication

- Use any existing signed-in Intel / Workday session if present.
- Because Intel is a newly added site, first-run credential entry must be completed by the user or by the browser/password manager. Do not type email, password, verification, MFA, CAPTCHA, or account-creation fields automatically.
- If the Intel / Workday sign-in dialog is visible and the email/password fields are already populated by the browser or password manager, click the visible `Sign In`, `Submit`, `Continue`, or equivalent login action and then re-read the live page.
- If the sign-in dialog is visible but required credential fields are empty, hidden behind a password-manager selection, or still need user choice, stop with `blocked`, asking the user to complete login in the browser.
- If no signed-in Intel / Workday state is visible, open the visible sign-in entry when needed, then either continue with already-populated credentials as above or stop with `blocked`.
- After the user confirms login is complete, resume from the current live page and reuse the saved browser profile for later runs.
- Do not treat a public Intel jobs listing alone as proof of login when the user requested applying. Look for a signed-in identity surface, candidate home, profile menu, account menu, or Workday candidate context.

### Ready Signal

- End `Session Preparation` only when the browser is inside the Intel jobs system and signed-in identity is visible, or when a later user-approved non-apply retrieval-only run explicitly does not require login.
- If the user requested applying and no signed-in state is visible, stop as `blocked` for user login takeover before continuing.

## Channel Discovery

### Navigation

- Start from `https://jobs.intel.com/en` or the current Intel entry URL.
- Follow official Intel careers redirects into the real jobs surface.
- If the site redirects to Intel Workday, stay inside the Intel Workday jobs system.
- If the current page already shows a real jobs search UI, filters, pagination, or visible job cards, discovery is complete.
- Do not browse Intel marketing, benefits, culture, legal, privacy, or general company pages once a real jobs surface is found.

### Success Signal

- A usable jobs surface has visible search, filters, job cards/list rows, or a stable Intel Workday jobs listing URL.

## Job Filtering

### Filtering Goal

- Apply the project role target as much as Intel exposes through visible filters/search.
- Prefer visible filters over free-text search when reliable.

### Filtering Direction

- Use `Location = China` if available.
- Use `Full time` / regular employment type if available.
- Use Software, Engineering, AI, Machine Learning, Platform, Systems, Cloud, or similar engineering categories when Intel exposes category/team filters.
- If Intel exposes sort, prefer newest/recently posted.
- If a filter opens a dialog, select the concrete visible option and use that dialog's own apply/view-results action.
- Stop filtering when the live Intel job list reflects the available China/full-time/engineering narrowing to the extent the site exposes it.
- If a desired filter is not present, record that limitation and proceed to retrieval instead of looping.

## Job Retrieval

- Record the full current Intel results page before any stop decision.
- Do not open job detail pages merely to enrich JD text during retrieval.
- Record stable fields visible on the listing: title, URL, Intel/Workday job id if visible, location, posted label/date, and employment type.
- If the listing page lacks a visible job id but has a stable job URL, record the URL and continue.
- If a page contains jobs already known locally, still preserve any new or incomplete-history jobs from that page before stopping.
- If the page shows no jobs, no results, or a closed-position message, record the evidence and finish the phase.

## Apply

### Matching Override

- Use the project matching rule against the live Intel JD.
- Apply only after the role is judged `recommended_apply`.
- If the Intel live page says the role is closed, unavailable, no longer accepting applications, or zero jobs found for the direct job URL, record `application_status = closed`, `apply_state = terminal_closed`, and `decision_reason_type = closed`.

### Form Filling

- Use the staged resume PDF from the workspace when Intel asks for a resume/CV.
- Use `workspace/profile/application_profile.md` for stable form facts:
  - gender: `Male`
  - country/nationality/residence: `China`
  - state/province: `Shanxi`
  - city: `Taiyuan`
  - postal code: `030000`
  - standard policy/rules acknowledgement: `Yes`
  - legally authorized to work where profile says yes; visa sponsorship: `No`
  - non-compete / non-solicitation restrictions: use `has_non_compete_or_non_solicitation` from the profile (`No`)
  - previous Intel or named affiliate/employer history: use `has_previous_target_company_employment` from the profile (`No`)
  - immediate-family or close relationship with Intel / named partner / target-company personnel: use `has_immediate_family_relationship_at_target_company` from the profile (`No`)
  - IP ownership, invention ownership, financial/economic interest, or similar Intel-relevant interest: use `has_relevant_ip_or_economic_interest` from the profile (`No`)
  - intent to keep secondary non-Intel employment or non-Intel business activity after hire: use `intends_secondary_non_target_employment_or_business_activity` from the profile (`No`)
  - government official relationship for Intel government-related business: use `has_government_official_relationship_for_target_business` from the profile (`No`)
- Use site-specific visible labels when they differ, but do not invent unsupported experience.
- For source/how-did-you-hear-about-us questions, choose the most neutral professional/source option when visible. If no specific source is required and options are ordinal-only, use the default non-first generic option rather than spending excessive time.
- Request `full_cv` only for required role-specific or open-ended questions that cannot be answered from lightweight facts and persona context.
- If Intel asks for human-only login, verification, CAPTCHA, MFA, or unsupported personal facts, stop that job as `blocked`.

### Site Signals

- If Intel shows already-applied, view application, application received, submitted, or similar successful state, record the job as already applied/submitted and move to the next apply target.
- Treat an application as submitted only after Intel shows an explicit success/received/submitted confirmation after the final submit.
- On successful submit, write `application_status = submitted`, `apply_state = terminal_submitted`, `decision_status = recommended_apply`, and preserve exact Intel text in `application_status_raw`.
- Do not write raw Intel/Workday phrases into canonical status fields.

### Escalation

- Stop and ask for user takeover when the next required step is unsafe, ambiguous, login-human-only, CAPTCHA/MFA, or a required answer cannot be supported by local profile/CV/persona evidence.
