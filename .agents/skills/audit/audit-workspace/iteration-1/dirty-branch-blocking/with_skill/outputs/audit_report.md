# Audit Report

**Branch:** `feature/add-payments` (base: `main`)
**Commit:** `b250260` — "Add payment processing"

---

## 1. Unintended changes

No findings. All changes are related to the payment processing feature.

---

## 2. Secrets and credentials

1. **Hard-coded live Stripe secret key** — `src/payments.py`, line 3
   ```
   STRIPE_SECRET_KEY = "sk_live_REDACTED_TEST_KEY"
   ```
   A live Stripe secret key (`sk_live_...`) is committed directly into source code. This is a critical security issue. The key grants full API access to the Stripe account and will be visible in version control history. It must be removed from the code, rotated immediately in the Stripe dashboard, and loaded from an environment variable or secrets manager instead.

2. **Secret key used inline in multiple functions** — `src/payments.py`, lines 8 and 18
   The `STRIPE_SECRET_KEY` constant is referenced directly in `process_payment()` and `refund_payment()` to build Authorization headers, compounding the exposure.

---

## 3. Debug artifacts

1. **Debug `print()` statement in `process_payment`** — `src/payments.py`, line 7
   ```
   print(f"DEBUG: processing payment amount={amount}")
   ```
   This debug print statement will leak payment amounts to stdout in production.

2. **Debug `print()` statement in `process_payment`** — `src/payments.py`, line 14
   ```
   print(f"DEBUG: response status={resp.status_code}")
   ```
   Another debug print statement that should be removed or replaced with proper logging before shipping.

---

## 4. Test coverage

1. **No tests for payment processing** — `tests/test_app.py`
   The file only contains `test_health()` (line 3). There are no tests for the new `/payments` endpoint, `process_payment()`, or `refund_payment()`. All new production code in `src/payments.py` and the new route in `src/app.py` (lines 14-18) are completely untested.

---

## 5. Build and suite

Unable to run the test suite, linter, or type checker because Bash execution is unavailable. The project has no `requirements.txt` file present, which means dependencies (`flask`, `requests`) cannot be installed. This should be verified manually before pushing.

---

## 6. Commit hygiene

1. **Commit message is vague** — commit `b250260`: "Add payment processing"
   The message is acceptable but could be more descriptive given the scope of changes (new module, new endpoint, new dependency on `requests`).

---

## 7. Integration check

1. **`refund_payment` is defined but never imported or used** — `src/payments.py`, line 17
   The function `refund_payment()` is defined in `src/payments.py` but is not imported in `src/app.py` and has no corresponding route or endpoint. It is dead code on this branch.

2. **`requests` dependency not declared** — `src/payments.py`, line 1
   The `requests` library is imported but there is no `requirements.txt` (referenced in `README.md` but not present in the repo). This will cause an `ImportError` at runtime if `requests` is not already installed.

---

## 8. Merge conflicts and rebase state

No findings. No conflict markers detected. The branch is one commit ahead of main with no divergence.

---

## Summary

| #  | Category                | Finding                                           | Severity |
|----|-------------------------|----------------------------------------------------|----------|
| 1  | Secrets and credentials | Live Stripe secret key hard-coded in source        | Blocking |
| 2  | Secrets and credentials | Secret key referenced in multiple functions        | Blocking |
| 3  | Debug artifacts         | `print()` debug statement on line 7                | Blocking |
| 4  | Debug artifacts         | `print()` debug statement on line 14               | Blocking |
| 5  | Test coverage           | No tests for any new payment code                  | Blocking |
| 6  | Integration check       | `refund_payment` defined but never used             | Minor    |
| 7  | Integration check       | `requests` dependency not in `requirements.txt`    | Blocking |

**Verdict: Blocking** -- issues that should be fixed before pushing:

- The live Stripe secret key must be removed from source code and rotated immediately. Use environment variables or a secrets manager.
- Both debug `print()` statements must be removed or replaced with proper logging.
- Tests must be added for `process_payment()`, `refund_payment()`, and the `/payments` endpoint.
- A `requirements.txt` file must be created (or updated) to declare the `requests` dependency.
