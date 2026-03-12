# Code Review: feature/add-user-validation

## Branch Summary

**Branch:** `feature/add-user-validation`
**Base:** `main`
**Commits:** 1 commit -- `feat: add input validation for user creation`

This branch adds input validation for user creation, including email format validation and name length validation, with corresponding unit tests.

---

## Changes Overview

### New Files

#### `src/validators.py`
A new module providing validation functions and a custom exception:

- **`ValidationError(Exception)`** -- Custom exception for validation failures.
- **`validate_email(email: str) -> str`** -- Validates email format using a regex pattern. Raises `ValidationError` for invalid emails.
- **`validate_name(name: str) -> str`** -- Validates that a name is at least 2 characters after stripping whitespace. Raises `ValidationError` for empty or too-short names.

#### `tests/test_validators.py`
Unit tests covering the validator functions:

- `test_valid_email` -- Verifies a well-formed email passes validation.
- `test_invalid_email` -- Verifies that a malformed string raises `ValidationError`.
- `test_valid_name` -- Verifies a normal name passes validation.
- `test_short_name` -- Verifies a single-character name raises `ValidationError`.
- `test_empty_name` -- Verifies an empty string raises `ValidationError`.

### Modified Files

#### `src/repository.py`
Updated to integrate validation into the `add_user` function:

- Added imports: `validate_email` and `validate_name` from `src.validators`.
- `add_user()` now calls `validate_email(user.email)` and `validate_name(user.name)` before appending the user to the in-memory list.

#### `tests/test_repository.py`
Updated/added repository-level tests that exercise validation:

- `test_add_and_get_user` -- Adds a valid user and retrieves by ID.
- `test_add_user_invalid_email` -- Confirms that adding a user with an invalid email raises `ValidationError`.
- `test_list_users` -- Verifies that `list_users()` returns at least one user.

### Unchanged Files

#### `src/models.py`
The `User` dataclass (id, name, email) -- no changes in this branch.

---

## Code Review Findings

### Positive Aspects

1. **Clean separation of concerns.** Validation logic is in its own module (`validators.py`), separate from the data model and repository. This makes the validators independently testable and reusable.

2. **Good test coverage.** Both the validators and the repository integration are tested. The tests cover valid inputs and key invalid-input edge cases (invalid email, short name, empty name).

3. **Consistent error handling.** A custom `ValidationError` exception is used throughout, making it easy for callers to catch validation-specific errors.

4. **Clear commit message.** The commit message is descriptive and includes a body explaining the purpose and scope.

### Issues and Suggestions

#### 1. Test Isolation -- Shared Mutable State (Medium)
The `_users` list in `repository.py` is module-level mutable state. Tests that call `add_user()` will permanently modify this list for the duration of the test session. This causes:

- `test_list_users` depends on `test_add_and_get_user` having run first (it asserts `len(list_users()) >= 1`).
- Test ordering matters, which makes tests fragile and non-deterministic if run in isolation or in different order.

**Suggestion:** Add a `clear_users()` or `reset()` function to the repository module, and use a pytest fixture to reset state before each test. Alternatively, consider using a fixture that patches `_users`.

#### 2. `validate_name` Return Value Not Used (Low)
`validate_name()` strips whitespace and returns the cleaned name, but in `repository.py` the return value is discarded:
```python
validate_name(user.name)  # return value ignored
```
This means a user with a name like `"  Alice  "` will pass validation but be stored with the leading/trailing whitespace intact. Either the return value should be used to update the user's name, or `validate_name` should only validate without transforming.

#### 3. Email Regex -- Edge Cases (Low)
The email regex `r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'` is a reasonable basic pattern, but it will reject some valid email addresses (e.g., those with quoted strings, IP literal domains) and accept some invalid ones (e.g., consecutive dots in the local part like `a..b@example.com`). For most applications this is acceptable, but worth noting.

#### 4. No Duplicate User ID Check (Low)
`add_user()` does not check whether a user with the same `id` already exists. Calling `add_user` twice with the same ID will result in duplicates in the list, and `get_user` will always return the first one found. Consider adding a uniqueness check.

#### 5. `test_list_users` Assertion Is Weak (Low)
The assertion `assert len(list_users()) >= 1` is loose -- it passes as long as at least one user exists from a previous test. A stronger test would assert an exact count or check specific user contents after controlled setup.

---

## Verdict

**The branch looks good and is ready to push.** The code is clean, well-structured, and properly tested. The issues noted above are minor and could be addressed in follow-up work. None of them are blockers.

- No security concerns identified.
- No syntax errors or obvious bugs.
- No uncommitted or unstaged changes detected.
- The working tree appears clean.
