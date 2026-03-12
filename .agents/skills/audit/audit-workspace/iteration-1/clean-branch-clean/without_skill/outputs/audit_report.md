# Code Review: feature/add-user-validation

## Branch Summary

- **Branch:** `feature/add-user-validation`
- **Base:** `main`
- **Commit:** `9be6cb4c` -- "feat: add input validation for user creation"
- **Working tree:** Clean (no uncommitted changes)

## Overview

This branch adds input validation for user creation. It introduces a new `validators` module with email and name validation, integrates those validators into the repository layer, and includes corresponding unit tests.

## Changes

### New Files

#### `src/validators.py`
Adds a `ValidationError` exception class and two validation functions:
- `validate_email(email)` -- validates email format using a regex pattern, raises `ValidationError` on failure.
- `validate_name(name)` -- validates that the name is at least 2 characters (after stripping whitespace), raises `ValidationError` on failure.

#### `tests/test_validators.py`
Adds 5 unit tests covering:
- Valid email acceptance
- Invalid email rejection
- Valid name acceptance
- Short name rejection (single character)
- Empty name rejection

### Modified Files

#### `src/repository.py`
Updated to import and call `validate_email` and `validate_name` before persisting a user in `add_user()`.

#### `tests/test_repository.py`
Updated to include a test for adding a user with an invalid email (`test_add_user_invalid_email`), verifying that `ValidationError` is raised.

## Review Findings

### Positive Aspects

1. **Clean separation of concerns.** Validation logic is in its own module rather than being embedded in the repository or model layer. This makes it reusable and independently testable.

2. **Good test coverage for validators.** The validator tests cover valid inputs, invalid inputs, and edge cases (empty string, single character).

3. **Clear commit message.** The commit message and its body accurately describe the change.

4. **Consistent code style.** Type hints are used consistently, and the code follows Python conventions.

### Issues and Suggestions

#### 1. Module-level mutable state in `repository.py` (Medium)
The `_users` list is a module-level global. This means:
- Tests that call `add_user()` will accumulate state across test runs within the same process.
- `test_list_users` uses `assert len(list_users()) >= 1` which is fragile -- it depends on `test_add_and_get_user` running first and polluting the global list.

**Recommendation:** Consider using a class-based repository pattern or adding a `clear_users()` / reset function. At minimum, add a test fixture that resets `_users` between tests.

#### 2. Email regex could reject valid addresses (Low)
The regex `r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'` is a reasonable basic check but will reject some valid email addresses (e.g., those with quoted strings, IP address domains, or internationalized domain names). This is acceptable for many applications, but it should be documented as a deliberate simplification.

#### 3. No duplicate user ID check (Low)
`add_user()` does not check whether a user with the same `id` already exists. Adding a duplicate would silently create two users with the same ID, making `get_user()` always return the first one. Consider adding a uniqueness check.

#### 4. `validate_name` allows whitespace-only names of sufficient length (Low)
`validate_name` strips the name and checks `len(name.strip()) < 2`, but it returns `name.strip()`. If someone passes `"   "` (three spaces), it will be stripped to `""` and correctly rejected. However, `"  ab  "` will pass and return `"ab"`, which may or may not be desired. The behavior is reasonable but worth documenting.

#### 5. No `__init__.py` files (Info)
The `src/` and `tests/` directories do not have `__init__.py` files. Depending on the project's package structure and test runner configuration, this may or may not be an issue. If using pytest with default settings and the project root in `sys.path`, this should work, but adding `__init__.py` files would make the package structure explicit.

## Verdict

**Ready to push with minor considerations.** The code is clean, well-structured, and well-tested. The issues noted above are minor and could be addressed in follow-up work. The most actionable item is the shared mutable state in `repository.py` and the test ordering dependency, which could cause flaky tests as the test suite grows.
