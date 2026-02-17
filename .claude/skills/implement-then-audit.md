# Implement-Then-Audit Loop

A structured pattern for building features with high spec fidelity. An implementation sub-agent builds the feature, then an audit sub-agent reviews it against the spec. Fixes are applied and re-audited until the auditor is 100% satisfied, or a maximum of 5 cycles is reached.

## Usage

```
/implement-then-audit <description of what to implement> [--spec <path-to-spec-file>]
```

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `description` | Yes | A clear description of the feature or phase to implement. |
| `--spec <path>` | No | Path to the spec file to audit against. Defaults to `specs/tasche-spec.md` if it exists. |

**Examples:**

```
/implement-then-audit "Add FTS5 full-text search with relevance ranking"
/implement-then-audit "Build the TTS listen-later feature" --spec specs/tasche-spec.md
/implement-then-audit "Implement GitHub OAuth authentication flow"
```

## The Pattern

This skill executes a two-agent loop: an **implementer** that writes code and an **auditor** that reviews it. The key insight is that the auditor checks against the **spec**, not just whether the code "works."

### Step 1: Preparation

Before starting the loop, gather context:

1. **Read the spec.** If `--spec` was provided, read that file. Otherwise, look for a spec file in `specs/` or ask the user. Identify the specific section(s) relevant to the description.
2. **Extract acceptance criteria.** From the spec section, build a concrete list of what "done" looks like. Include:
   - Every endpoint, field, status value, and behavior mentioned
   - Any SQL schemas, CHECK constraints, or enum values
   - Security requirements (auth, input validation, SSRF, XSS)
   - Error handling and edge cases
   - Integration points with existing code
3. **Identify existing code.** Search the codebase for related modules, helpers, and conventions. The implementation must follow existing patterns.

### Step 2: Implementation (Implementer Role)

Launch a sub-agent (Task tool, subagent_type=general-purpose) to build the feature:

1. **Write the code.** Implement the feature according to the spec and extracted acceptance criteria. Follow all project conventions from `CLAUDE.md`.
2. **Write tests.** Add unit tests covering happy paths, error cases, and edge cases.
3. **Run tests.** Execute the test suite and confirm all tests pass.
4. **Run lint.** Ensure code quality.
5. **Summarize what was built.** List all files created or modified, key design decisions, and any spec deviations.

### Step 3: Audit (Auditor Role)

Launch a separate sub-agent to review the implementation against the spec. The auditor is a distinct role -- approach the code as if seeing it for the first time, with the spec as the source of truth.

**Audit Checklist:**

#### 1. Correctness
- Does the code do what the spec says? Not "does it work" but "does it match the spec's intent?"
- Are status strings, enum values, and field names exact matches to the spec and DB CHECK constraints?
- Are error responses correct (status codes, error messages, edge case handling)?

#### 2. Completeness
- Are ALL items from the relevant spec section implemented? Go line by line through the spec.
- Are there endpoints, fields, or behaviors mentioned in the spec that are missing?
- Does the implementation cover the full lifecycle (create, read, update, delete, error, retry)?

#### 3. Security
- Input validation: Are all text fields length-limited? Are URLs validated?
- XSS: Is user content sanitized before rendering?
- SSRF: Are private/internal network URLs blocked when fetching user-provided URLs?
- Auth: Are all endpoints properly gated?
- SQL injection: Are all queries parameterized? Is FTS5 input sanitized?

#### 4. Code Quality
- No code duplication -- are existing helpers imported rather than copied?
- Does the code follow the project's established patterns?
- Are there hardcoded values that should be constants or config?

#### 5. Tests
- Do tests cover both happy paths and error cases?
- Do tests verify spec-specific behaviors (exact status strings, error codes)?
- Do tests actually assert meaningful things (not just "no exception thrown")?

#### 6. Spec Fidelity
- **This is the most important category.** Re-read the spec section one more time and compare it to the implementation line by line.
- Are there any interpretations that differ from the spec's plain reading?
- Are there "creative additions" not in the spec that could introduce bugs?
- Do URL paths, query parameters, and response shapes match the spec exactly?

**Audit Output Format:**

Rate each issue as CRITICAL, HIGH, MEDIUM, or LOW:

```
## Audit Result: FAIL (or PASS)

### Issues Found

1. **[HIGH] Status string mismatch** -- `audio_status` uses `'processing'` but spec says `'generating'`.
   File: `src/tts/processing.py`, line 42.
   Fix: Change `"processing"` to `"generating"`.

2. **[MEDIUM] Missing endpoint** -- Spec requires `DELETE /api/tags/{tag_id}` but no deletion endpoint exists.
   Fix: Add delete handler in `src/tags/routes.py`.
```

**Pass/Fail Criteria:**
- **PASS:** Zero CRITICAL or HIGH issues. Any MEDIUM/LOW issues are cosmetic or deferred.
- **FAIL:** Any CRITICAL or HIGH issue, OR 3+ MEDIUM issues that together represent a significant gap.

### Step 4: Fix and Re-Audit (Loop)

If the audit fails:

1. **Fix every issue** identified by the auditor. Address them in priority order (CRITICAL first).
2. **Re-run tests** to confirm fixes don't break anything.
3. **Re-run the audit** from Step 3. The auditor should verify each previously-identified issue is resolved AND check that fixes didn't introduce new issues.
4. **Repeat** until the auditor reports PASS.

**Maximum cycles:** 5 (implement + up to 4 fix-and-re-audit rounds). If still failing after 5 cycles, stop and report remaining issues to the user.

### Step 5: Completion

Once the audit passes:

1. **Report the result** to the user:
   - Number of cycles required
   - Files created or modified
   - Any deferred items (MEDIUM/LOW issues accepted as-is)
   - Key lessons or patterns discovered
2. **Do not commit automatically.** Let the user review and decide when to commit.

## Why This Pattern Works

Based on experience across 14 phases of the Tasche project:

- **Tests pass but audits catch intent gaps.** All tests passed throughout every phase, yet audits found XSS vulnerabilities, missing features, wrong status strings, and broken bookmarklets. Tests verify behavior; audits verify completeness and spec fidelity.
- **Most phases need exactly one fix cycle.** 7 of 9 phases required implement -> audit -> fix -> re-audit -> PASS. Plan for two cycles as the normal case.
- **Self-contained features pass on first attempt.** Observability (middleware) and hardening (input validation) passed audit immediately. Cross-cutting features (frontend + backend) needed more cycles.
- **Enum/status string mismatches are the most common bug.** Three separate phases had status strings that didn't match the spec or DB constraints.

## Common Pitfalls

These issues appeared repeatedly and should be checked in every audit:

1. **Status enum mismatches** between code, DB CHECK constraints, and the spec
2. **Duplicated helper functions** instead of imports from existing modules
3. **Missing input validation** (field length limits, URL scheme validation)
4. **XSS in post-processing** (content escaped but re-introduced via regex)
5. **Idempotency not enforced** for expensive operations (TTS, content processing)
6. **Wrong deletion order** across stores (delete data before references, never reverse)
7. **FTS5 search** not using `INNER JOIN` with `ORDER BY rank`
8. **Cross-origin code** using wrong URL patterns for bookmarklets/share targets
