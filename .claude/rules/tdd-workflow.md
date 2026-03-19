---
description: Use test-driven development for bug fixes and non-trivial changes
globs: src/**/*.py, tests/**/*.py
---

# Development Workflow: TDD

Use test-driven development for all bug fixes and non-trivial changes:

1. **Write a failing test first** that demonstrates the bug or specifies the behavior
2. **Run the test, confirm it fails** for the right reason
3. **Write the minimum code** to make the test pass
4. **Run the full suite** to confirm no regressions

This applies to bug fixes (write a test that reproduces the bug before fixing it)
and new store/sync features (specify the interface in tests before implementing).
Skip TDD only for trivial changes (typos, config, display-only code).
