# Audit Skill for Claude Code

A comprehensive audit toolkit with 14 audit types. Catches issues before you push, and goes deep when you need it.

## What it does

**Branch audit** (default) — Reviews your branch diff against 8 categories and gives a Clean/Minor/Blocking verdict:

- Secrets and credentials
- Unintended changes
- Debug artifacts
- Test coverage
- Build and suite
- Commit hygiene
- Integration check
- Merge conflicts and rebase state

**Deep-dive audits** — 13 project-wide analyses using sub-agents:

- **Code quality** — duplication, inconsistency, simplification opportunities
- **Documentation brittleness** — fragile references, over-specified details, staleness risk
- **Documentation-code sync** — docs that have already drifted from the code
- **Language best practices** — idiomatic patterns for Python, JS/TS, Go, Rust, Java, Ruby, Shell
- **Concurrency** — shared mutable state, race conditions, deadlock risk, thread/goroutine leaks
- **Resource management** — file handle leaks, unclosed connections, zombie processes, temp files
- **Test quality** — weak assertions, flaky patterns, test isolation, missing negative tests
- **Feature completeness** — documented features vs actual implementation, missing endpoints
- **Performance** — N+1 queries, unbounded caches, hot-path allocations, blocking I/O
- **Bug patterns** — shallow merges, serialization mismatches, silent data loss, stale closures
- **Design philosophy compliance** — evaluate code against the project's own stated principles
- **Security vulnerabilities** — injection, auth, data exposure, dependency risks, config
- **UI design** — review using CRAP principles (Contrast, Repetition, Alignment, Proximity)

## Installation

### Via Claude Code plugin marketplace

```
/plugin marketplace add adewale/audit-skill
/plugin install audit@adewale-audit
```

### Via skills.sh

```bash
npx skills add adewale/audit-skill
```

### Manual

Copy `SKILL.md` into your project's `.claude/skills/` directory:

```bash
mkdir -p .claude/skills/audit
curl -o .claude/skills/audit/SKILL.md https://raw.githubusercontent.com/adewale/audit-skill/main/SKILL.md
```

## Usage

Invoke with `/audit` or ask Claude Code directly:

```
> /audit
> audit
> review my changes before I push
> check what I'm about to push
> run a concurrency audit
> audit test quality
> do a security audit
> check for performance issues
> are our docs up to date with the code?
> review the UI design
```

## Example output

### Branch audit — Blocking verdict

```
## Summary

| #  | Category                | Finding                                        | Severity |
|----|-------------------------|------------------------------------------------|----------|
| 1  | Secrets and credentials | Live Stripe secret key hard-coded in source    | Blocking |
| 2  | Debug artifacts         | print() debug statement on line 7              | Blocking |
| 3  | Test coverage           | No tests for any new payment code              | Blocking |

Verdict: **Blocking** — issues that should be fixed before pushing.
```

### Branch audit — Clean verdict

```
Verdict: **Clean** — no findings, safe to push.
```

## Eval results

Tested across 6 scenarios with and without the skill:

**Branch audits** (3 scenarios: dirty/clean/mixed branches):

| Configuration  | Pass rate | Avg time | Avg tokens |
|----------------|-----------|----------|------------|
| With skill     | 93%       | 112s     | 21,619     |
| Without skill  | 73%       | 94s      | 17,717     |

**Deep-dive audits** (3 scenarios: concurrency/test quality/performance):

| Configuration  | Pass rate | Avg time | Avg tokens |
|----------------|-----------|----------|------------|
| With skill     | 100%      | 131s     | 31,383     |
| Without skill  | 100%      | 133s     | 24,219     |

The skill's primary value is consistent structured output — Clean/Minor/Blocking verdicts, systematic checklists, and organized report formats. Detection of issues is similar either way since Claude is already good at finding problems. The skill ensures they're reported in a reliable, repeatable format.

## License

MIT
