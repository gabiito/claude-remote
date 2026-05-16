# Skill Registry — claude-remote

Generated: 2026-05-14
Source: `~/.claude/skills/` (user-level)

## User Skills — Trigger Table

| Skill | Trigger | Path |
|-------|---------|------|
| branch-pr | creating, opening, or preparing PRs for review | `~/.claude/skills/branch-pr/SKILL.md` |
| chained-pr | PRs over 400 lines, stacked PRs, review slices | `~/.claude/skills/chained-pr/SKILL.md` |
| cognitive-doc-design | writing guides, READMEs, RFCs, onboarding, architecture, review-facing docs | `~/.claude/skills/cognitive-doc-design/SKILL.md` |
| comment-writer | PR feedback, issue replies, reviews, Slack messages, GitHub comments | `~/.claude/skills/comment-writer/SKILL.md` |
| go-testing | Go tests, go test coverage, Bubbletea teatest, golden files | `~/.claude/skills/go-testing/SKILL.md` |
| issue-creation | creating GitHub issues, bug reports, or feature requests | `~/.claude/skills/issue-creation/SKILL.md` |
| judgment-day | judgment day, dual review, adversarial review, juzgar | `~/.claude/skills/judgment-day/SKILL.md` |
| skill-creator | new skills, agent instructions, documenting AI usage patterns | `~/.claude/skills/skill-creator/SKILL.md` |
| work-unit-commits | implementation, commit splitting, chained PRs, keeping tests and docs with code | `~/.claude/skills/work-unit-commits/SKILL.md` |

## Compact Rules

### branch-pr

- Every PR MUST link an approved issue (`Closes #N`, issue has `status:approved`).
- Every PR MUST have exactly one `type:*` label matching its conventional-commit type.
- Branch name regex: `^(feat|fix|chore|docs|style|refactor|perf|test|build|ci|revert)\/[a-z0-9._-]+$`.
- Commit messages match conventional-commits regex: `^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([a-z0-9\._-]+\))?!?: .+`.
- PR body MUST include linked issue, type checkbox, summary, changes table, test plan, contributor checklist.
- No `Co-Authored-By` trailers.

### chained-pr

- Split PRs over **400 changed lines** unless maintainer grants `size:exception`.
- Each PR reviewable in ≤60 minutes; one deliverable work unit per PR; keep tests/docs with the unit.
- Every chained PR states start, end, prior deps, follow-up, out-of-scope, with `📍` marker on current node.
- Feature Branch Chain: draft tracker PR; child #1 targets tracker; later children target the immediate parent branch.
- Stacked PRs to main when slices can land independently.
- Polluted diff = base bug: retarget or rebase until only the current unit appears.
- Do not mix chain strategies after the user chooses one.

### cognitive-doc-design

- Lead with the answer (decision/action/outcome first); context after.
- Progressive disclosure: happy path → details → edge cases → references.
- Chunk into small sections; use headings, callouts, summaries for signposting.
- Prefer tables, checklists, examples, templates over remembered prose.
- For PR/review docs: state what to review first and what's out of scope; link prev/next PR in chains.

### comment-writer

- Be useful fast: actionable point first, then why, then next action.
- 1–3 short paragraphs OR tight bullet list; explain technical why on change requests.
- Match thread language; Spanish = Rioplatense voseo (`podés`, `tenés`, `fijate`, `dale`).
- No em dashes; use commas, periods, parentheses instead.
- Comment on highest-value issue, not every tiny preference.

### go-testing

- Not applicable to this project (Python stack). Skip.

### issue-creation

- Blank issues disabled — MUST use bug-report or feature-request template.
- Issues get `status:needs-review` automatically; maintainer adds `status:approved` before a PR can be opened.
- Questions go to Discussions, not issues.
- Search for duplicates before filing.

### judgment-day

- Trigger only on explicit request (`judgment day`, `juzgar`, `que lo juzguen`).
- Launch two blind judges in parallel with identical target/criteria; never review yourself.
- Inject compact project rules into BOTH judge prompts AND fix prompts.
- Classify WARNING (real) only if normal use can trigger it; otherwise downgrade to INFO.
- Ask before Round 1 fixes; re-judge in parallel after each fix; terminal states are APPROVED or ESCALATED only.
- After 2 fix iterations with remaining issues, ask the user whether to continue.

### skill-creator

- Skill = runtime LLM instruction contract, not human docs.
- Frontmatter: `name`, `description` (one quoted line, trigger-first, ≤250 chars), `license`, `metadata.author`, `metadata.version`.
- Sections in order: Activation Contract, Hard Rules, Decision Gates, Execution Steps, Output Contract, References.
- Body target 180–450 tokens; hard max 1000.
- Templates/schemas → `assets/`; conceptual detail/edge cases → `references/`. No `Keywords` section.

### work-unit-commits

- Commit by work unit (deliverable behavior/fix/migration/docs), not by file type (`models`/`services`/`tests`).
- Tests live in the same commit as the behavior they verify; docs live with the user-visible change.
- Each commit should pass standalone and be rollback-safe without touching unrelated work.
- If SDD tasks forecast >400 changed lines, group commits into chained PR slices BEFORE implementation.
- Use conventional-commit messages; the message explains outcome, not file list.
