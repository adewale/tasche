# Changelog

All notable changes to Audit Skill are documented in this file.

## [1.0.0] - 2026-03-09

### Added

- **Branch audit** (default) with 8-category pre-push checklist and Clean/Minor/Blocking verdicts
  - Secrets and credentials
  - Unintended changes
  - Debug artifacts
  - Test coverage
  - Build and suite
  - Commit hygiene
  - Integration check
  - Merge conflicts and rebase state
- **13 deep-dive audits** run via sub-agents
  - Code quality (duplication, inconsistency, simplification)
  - Documentation brittleness (fragile refs, over-spec, staleness risk)
  - Documentation-code sync (API, setup, architecture, config, examples)
  - Language best practices (Python, JS/TS, Go, Rust, Java, Ruby, Shell)
  - Concurrency (shared state, race conditions, deadlock, thread leaks)
  - Resource management (file handles, connections, subprocesses, temp files)
  - Test quality (weak assertions, flakiness, isolation, negative tests)
  - Feature completeness (documented vs implemented)
  - Performance (N+1 queries, unbounded caches, hot-path allocations, blocking I/O)
  - Bug patterns (shallow merges, serialization, silent data loss, stale closures)
  - Design philosophy compliance (evaluate against project's stated principles)
  - Security vulnerabilities (injection, auth, data exposure, dependencies, config)
  - UI design (CRAP principles: Contrast, Repetition, Alignment, Proximity)
- Secret redaction guidance to prevent credential exposure in audit reports
- Plugin marketplace configuration (`.claude-plugin/`)
- Eval workspace with 3 iterations of benchmark data (93-100% pass rates)
- MIT license

[1.0.0]: https://github.com/adewale/audit-skill/releases/tag/v1.0.0
