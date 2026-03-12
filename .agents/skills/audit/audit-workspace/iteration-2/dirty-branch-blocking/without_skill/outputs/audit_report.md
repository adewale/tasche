# Audit Report: feature/add-payments

**Repository:** /Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-dirty
**Branch:** feature/add-payments
**Base Branch:** main
**Date:** 2026-03-07

---

## Branch Summary

The `feature/add-payments` branch was created from `main` and contains **1 commit**:

| Commit | Message | Author |
|--------|---------|--------|
| b250260 | Add payment processing | Ade Oshineye |

The `main` branch contains the initial commit (`7e77e60` -- "Initial commit: Flask API with health endpoint").

---

## Files Changed (relative to main)

| File | Change Type | Description |
|------|-------------|-------------|
| `src/payments.py` | **Added** | New payment processing module |
| `src/app.py` | **Modified** | Added payments import and `/payments` endpoint |

---

## Detailed Code Review

### 1. `src/payments.py` (New File)

This file introduces payment processing via the Stripe API. It contains two functions: `process_payment` and `refund_payment`.

### 2. `src/app.py` (Modified)

A new `POST /payments` endpoint was added, which accepts JSON with `amount`, `currency`, and `token` fields, and delegates to `process_payment`.

---

## Issues Found

### CRITICAL

#### 1. Hardcoded Secret API Key (Security -- Severity: CRITICAL)
**File:** `src/payments.py`, line 3
```python
STRIPE_SECRET_KEY = "sk_live_REDACTED_TEST_KEY"
```
A **live Stripe secret key** is hardcoded directly in the source code. This is a severe security vulnerability:
- The key will be stored in git history permanently once merged.
- Anyone with repository access gains full access to the Stripe account.
- This key appears to be a **live** key (prefix `sk_live_`), not a test key (`sk_test_`).

**Recommendation:** Remove the key immediately. Use environment variables or a secrets manager (e.g., `os.environ["STRIPE_SECRET_KEY"]`). The key should also be rotated on the Stripe dashboard since it has been committed to version control.

#### 2. No Input Validation (Security -- Severity: CRITICAL)
**File:** `src/app.py`, lines 16-18
```python
data = request.json
result = process_payment(data["amount"], data["currency"], data["token"])
```
- No validation that `request.json` is not `None` (will raise `TypeError` if body is missing or not JSON).
- No validation of `amount`, `currency`, or `token` fields (will raise `KeyError` if missing).
- No type checking -- `amount` could be negative, zero, or a non-numeric value.
- No sanitization of `currency` or `token` inputs.

**Recommendation:** Add input validation, type checking, and appropriate error handling. Return 400 responses for invalid input.

### HIGH

#### 3. No Error Handling on External API Calls (Reliability -- Severity: HIGH)
**File:** `src/payments.py`, lines 9-15 and 19-22
Neither `process_payment` nor `refund_payment` handle potential failures:
- No `try/except` around `requests.post()` (network errors, timeouts will crash the app).
- No check of `resp.status_code` before calling `resp.json()` (non-JSON error responses will raise exceptions).
- No timeout parameter on the `requests.post()` calls (could hang indefinitely).

**Recommendation:** Add timeout parameters, wrap in try/except, and check response status codes before parsing JSON.

#### 4. No Authentication/Authorization on Payment Endpoint (Security -- Severity: HIGH)
**File:** `src/app.py`, line 14
The `/payments` endpoint has no authentication or authorization. Anyone can submit payment requests.

**Recommendation:** Add authentication middleware (e.g., API key validation, JWT tokens, or session-based auth).

### MEDIUM

#### 5. Debug Print Statements Left in Code (Code Quality -- Severity: MEDIUM)
**File:** `src/payments.py`, lines 7 and 14
```python
print(f"DEBUG: processing payment amount={amount}")
print(f"DEBUG: response status={resp.status_code}")
```
Debug print statements should not be present in production code, especially in payment processing where they could log sensitive information.

**Recommendation:** Remove debug prints or replace with proper logging using Python's `logging` module at an appropriate log level.

#### 6. No Tests for Payment Functionality (Quality -- Severity: MEDIUM)
**File:** `tests/test_app.py`
The test file only contains a test for the `/health` endpoint. There are no tests for:
- The `/payments` endpoint.
- The `process_payment` function.
- The `refund_payment` function.
- Error cases or edge cases.

**Recommendation:** Add unit tests with mocked Stripe API calls, and integration tests for the payment endpoint.

### LOW

#### 7. Refund Endpoint Not Exposed (Completeness -- Severity: LOW)
**File:** `src/payments.py` defines `refund_payment`, but there is no corresponding route in `src/app.py` to expose it via the API.

**Recommendation:** Either add a `/refunds` endpoint in `src/app.py` or remove the unused function if it is not yet needed.

#### 8. No `.gitignore` Present (Code Quality -- Severity: LOW)
The repository has no `.gitignore` file, which increases the risk of accidentally committing sensitive files (e.g., `.env`, `__pycache__/`, virtual environments).

**Recommendation:** Add a `.gitignore` file appropriate for Python projects.

---

## Working Tree Status

Note: Bash access was denied during this audit, so I was unable to run `git diff` or `git status` to check for uncommitted changes in the working tree. The analysis above is based on the committed file contents and git reflog history. There may be additional staged or unstaged modifications not captured in this report.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 2 |
| HIGH | 2 |
| MEDIUM | 2 |
| LOW | 2 |

**Overall Assessment: This branch should NOT be merged in its current state.**

The hardcoded live Stripe secret key is the most urgent issue and represents an immediate security risk. The lack of input validation on the payment endpoint is also critical, as it could lead to unexpected behavior or exploitation. These issues must be resolved before this code is considered for merging into `main`.

### Recommended Actions (Priority Order)
1. **Immediately** remove the hardcoded Stripe secret key and rotate the key in the Stripe dashboard.
2. Add input validation and error handling to the payment endpoint and processing functions.
3. Add authentication/authorization to the payment endpoint.
4. Remove debug print statements.
5. Add comprehensive tests for payment functionality.
6. Add a `.gitignore` file.
