# Audit Report

## Branch Summary

- **Branch:** `feature/add-payments`
- **Base:** `main`
- **Commits:** 1 (`Add payment processing`)
- **Purpose:** Adds a Stripe-based payment processing module and exposes a `/payments` POST endpoint in the Flask API.

**Files changed:**
- `src/payments.py` (new file)
- `src/app.py` (modified — added payments import and `/payments` route)

---

## Findings

### Secrets and Credentials

| # | File | Line | Issue |
|---|------|------|-------|
| 1 | `src/payments.py` | 3 | **Hard-coded Stripe live secret key** `sk_live_REDACTED_TEST_KEY` is committed directly in source code. This is a production key (indicated by `sk_live_` prefix) and must be rotated immediately and moved to an environment variable or secrets manager. |

### Debug Artifacts

| # | File | Line | Issue |
|---|------|------|-------|
| 2 | `src/payments.py` | 7 | `print(f"DEBUG: processing payment amount={amount}")` — debug print statement left in production code. |
| 3 | `src/payments.py` | 14 | `print(f"DEBUG: response status={resp.status_code}")` — debug print statement left in production code. |

### Test Coverage

| # | File | Line | Issue |
|---|------|------|-------|
| 4 | `src/payments.py` | — | New module with two functions (`process_payment`, `refund_payment`) has no corresponding tests. `tests/test_app.py` only tests the `/health` endpoint. |
| 5 | `src/app.py` | 14-18 | New `/payments` POST route has no test coverage. |

### Build and Suite

No `requirements.txt`, `Pipfile`, `pyproject.toml`, or `setup.py` exists in the repository, so the test suite, linter, and type checker cannot be run. The README references `pip install -r requirements.txt` but the file is missing. Dependencies are not installable.

### Integration Check

| # | File | Line | Issue |
|---|------|------|-------|
| 6 | `src/payments.py` | 1 | `requests` is imported but not declared in any dependency file. No `requirements.txt`, `Pipfile`, or `pyproject.toml` exists in the project. |
| 7 | `src/payments.py` | 17-22 | `refund_payment()` is defined but never imported or referenced anywhere in the codebase — it is dead code with no route or caller. |

---

## Summary Table

| # | File | Line | Issue | Severity |
|---|------|------|-------|----------|
| 1 | `src/payments.py` | 3 | Hard-coded Stripe live secret key (`sk_live_...`) | **Blocking** |
| 2 | `src/payments.py` | 7 | Debug `print()` statement in production code | **Blocking** |
| 3 | `src/payments.py` | 14 | Debug `print()` statement in production code | **Blocking** |
| 4 | `src/payments.py` | — | No tests for `process_payment` or `refund_payment` | **Blocking** |
| 5 | `src/app.py` | 14-18 | No tests for `/payments` POST route | **Blocking** |
| 6 | `src/payments.py` | 1 | `requests` dependency not declared in any dependency file | Minor |
| 7 | `src/payments.py` | 17-22 | `refund_payment()` defined but never used | Minor |

## Verdict: Blocking

This branch has issues that **must be fixed before pushing**:

1. **A live Stripe secret key is hard-coded in source** (`src/payments.py:3`). This key must be revoked/rotated immediately and replaced with an environment variable or secrets manager lookup.
2. **Two debug `print()` statements** are present in production code (`src/payments.py:7`, `src/payments.py:14`). These should be removed or replaced with proper logging.
3. **No test coverage** exists for the new payments module or the `/payments` endpoint. Tests should be added for `process_payment`, `refund_payment`, and the route handler.
