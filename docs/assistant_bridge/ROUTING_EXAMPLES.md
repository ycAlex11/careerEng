# Assistant Bridge Routing Examples

These examples define how external assistants should route CareerEng-related messages.

| User Message | Category | Suggested Action |
| --- | --- | --- |
| `@career 检查投递状态` | `careereng_command` | `jobs_review_status` |
| `@career 查看一下投递情况` | `careereng_command` | `jobs_review_status` |
| `@career 检索投递已注册的网站` | `careereng_command` | `jobs_apply` |
| `@career 总结一下我们的投递情况` | `careereng_command` | `application_summary_build` |
| `@career 激活高通和 AMD` | `careereng_command` | `site_activate` |
| `@career 停用英伟达` | `careereng_command` | `site_deactivate` |
| `@career 我想投 AI infra，需要补什么？` | `career_intent_strategy` | save career strategy signal |
| `@career 我 CUDA 不强，想去 OpenAI AI infra` | `career_intent_strategy` | save career strategy signal |
| `@career 我最近做了一个 agent 项目，可以写进简历吗？` | `profile_resume_signal` | save profile/resume signal |
| `@career NVIDIA 这个岗位进入 in process 了` | `application_feedback` | save application feedback signal |
| `@career 帮我准备 NVIDIA SDET 面试` | `interview_record` | save interview prep event |
| `不是，我要 summary，不是去 dashboard 看` | `correction` | save correction event |

## Implicit Suggestions

When there is no `@career` and no active CareerEng thread scope, do not execute directly.

If the message strongly resembles a CareerEng task, call `assistant ingest` and ask the user for confirmation before running the suggested command.

Example:

User:

`看看最近投递怎么样了`

Assistant should suggest:

`This looks like a CareerEng application status check. Do you want me to run python -m careereng jobs review-status?`

