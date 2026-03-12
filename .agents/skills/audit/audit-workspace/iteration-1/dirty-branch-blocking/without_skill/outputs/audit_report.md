# Audit Report: `feature/add-payments` Branch

**Repository:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-dirty`
**Branch:** `feature/add-payments`
**Base branch:** `main`
**Date:** 2026-03-07

---

## Branch Summary

The `feature/add-payments` branch introduces payment processing functionality to a Flask API application. It contains one commit beyond `main`:

- **`b250260`** - "Add payment processing"

The base (`main`) branch contains a single initial commit:

- **`7e77e60`** - "Initial commit: Flask API with health endpoint"

---

## Files Changed

The following files were added or modified on this branch:

| File | Status | Description |
|------|--------|-------------|
| `src/payments.py` | **Added** | New payment processing module with Stripe integration |
| `src/app.py` | **Modified** | Added import of `process_payment` and new `/payments` endpoint; also contains a `/users` endpoint |
| `tests/test_app.py` | Likely unchanged or minimally modified | Only contains a health endpoint test |

---

## Critical Issues

### 1. CRITICAL -- Hardcoded Secret API Key (Security Vulnerability)

**File:** `src/payments.py`, line 3

```python
STRIPE_SECRET_KEY = "sk_live_REDACTED_TEST_KEY"
```

A **live Stripe secret key** is hardcoded directly in the source code. This is a severe security vulnerability:

- The key prefix `sk_live_` indicates this is a **production** key, not a test key.
- Anyone with access to this repository can make charges, issue refunds, and access sensitive customer data through the Stripe account.
- If this branch is merged and pushed to a remote, the key will be permanently exposed in git history (even if later removed from the file).

**Recommendation:**
- **Immediately rotate this key** in the Stripe dashboard.
- Move the key to an environment variable (e.g., `os.environ["STRIPE_SECRET_KEY"]`).
- Add a `.env` file to `.gitignore`.
- Consider using a secrets management solution (e.g., AWS Secrets Manager, HashiCorp Vault).
- Use `git filter-branch` or BFG Repo-Cleaner to scrub the key from git history if it has been pushed.

---

### 2. HIGH -- Debug Print Statements Left in Production Code

**File:** `src/payments.py`, lines 7 and 14

```python
print(f"DEBUG: processing payment amount={amount}")
print(f"DEBUG: response status={resp.status_code}")
```

Debug `print()` statements are left in the payment processing code. In a production environment:

- These will leak payment amounts and response statuses to stdout/logs.
- They indicate the code may not have gone through proper review.

**Recommendation:**
- Remove debug print statements.
- Use Python's `logging` module with appropriate log levels instead.

---

### 3. HIGH -- No Input Validation on Payment Endpoint

**File:** `src/app.py`, lines 14-18

```python
@app.route("/payments", methods=["POST"])
def create_payment():
    data = request.json
    result = process_payment(data["amount"], data["currency"], data["token"])
    return jsonify(result)
```

The `/payments` endpoint has no input validation:

- `request.json` can be `None` if the Content-Type header is missing or the body is not valid JSON, which would cause an unhandled `TypeError`.
- Direct dictionary access (`data["amount"]`, etc.) will raise an unhandled `KeyError` if any field is missing.
- There is no validation of `amount` (could be negative, zero, excessively large, or a non-numeric type).
- There is no validation of `currency` (could be an invalid currency code).
- There is no validation of `token` (could be empty or malformed).

**Recommendation:**
- Add null checks for `request.json`.
- Validate required fields exist and have correct types.
- Validate `amount` is a positive integer.
- Validate `currency` is a recognized currency code.
- Return proper 400 error responses for invalid input.

---

### 4. MEDIUM -- No Error Handling for External API Calls

**File:** `src/payments.py`, lines 9-15 and 19-22

The code makes HTTP requests to the Stripe API without any error handling:

- No `try/except` around `requests.post()` calls -- network errors, timeouts, and connection failures will result in unhandled exceptions.
- No timeout parameter on the `requests.post()` calls -- the application could hang indefinitely.
- `resp.json()` is called without checking if the response body is valid JSON.

**Recommendation:**
- Wrap API calls in try/except blocks to handle `requests.exceptions.RequestException`.
- Set explicit timeouts: `requests.post(..., timeout=30)`.
- Check `resp.status_code` before parsing the response.
- Return meaningful error responses to the caller.

---

### 5. MEDIUM -- No Authentication or Authorization on Payment Endpoint

**File:** `src/app.py`, line 14

The `/payments` endpoint accepts POST requests without any form of authentication or authorization. Anyone who can reach this endpoint can initiate charges.

**Recommendation:**
- Add authentication middleware (e.g., API key, JWT, session-based auth).
- Implement rate limiting to prevent abuse.

---

### 6. MEDIUM -- No Tests for Payment Functionality

**File:** `tests/test_app.py`

The test file only contains a test for the `/health` endpoint. There are no tests for:

- The `/payments` POST endpoint.
- The `process_payment()` function.
- The `refund_payment()` function.
- Edge cases (missing fields, invalid amounts, API failures).

**Recommendation:**
- Add unit tests for `process_payment()` and `refund_payment()` with mocked HTTP calls.
- Add integration tests for the `/payments` endpoint.
- Test error cases and edge cases.

---

### 7. LOW -- Unused `/users` Endpoint

**File:** `src/app.py`, lines 10-12

```python
@app.route("/users")
def list_users():
    return jsonify({"users": []})
```

There is a `/users` endpoint that returns a hardcoded empty list. This appears to be either a stub or placeholder. It may have been added as an uncommitted working-tree change (the repo appears to have dirty/uncommitted modifications).

**Recommendation:**
- If this is intentional, it should be implemented or documented.
- If it is a placeholder, consider removing it until the feature is ready.
- Ensure it is properly committed with a descriptive message.

---

### 8. LOW -- `refund_payment` Function Is Defined But Never Used

**File:** `src/payments.py`, lines 17-22

The `refund_payment()` function is defined but is not imported or called anywhere in the codebase. There is no corresponding endpoint in `app.py` and no tests for it.

**Recommendation:**
- If refund functionality is planned, add a corresponding endpoint and tests.
- If not needed yet, consider removing it to reduce dead code.

---

## Summary of Findings

| Severity | Count | Key Issues |
|----------|-------|------------|
| CRITICAL | 1 | Hardcoded live Stripe secret key |
| HIGH | 2 | Debug print statements; No input validation |
| MEDIUM | 3 | No error handling; No auth on payment endpoint; No tests for payments |
| LOW | 2 | Unused `/users` endpoint; Unused `refund_payment` function |

**Overall Assessment:** This branch has a **critical security vulnerability** (hardcoded live API key) that must be addressed before merging. The payment processing code also lacks input validation, error handling, authentication, and test coverage. These issues should be resolved before this branch is considered merge-ready.
