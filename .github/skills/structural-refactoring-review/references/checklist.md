# Refactoring Checklist

Use this checklist while reviewing or implementing structural cleanup in this repository.

## Favor Structural Fixes

- Fix behavior once in a shared helper, abstraction, or manager when multiple commands depend on it.
- Avoid copying the same conditional flow into `push`, `pull`, `docker-push`, `proxy`, `service`, or `monitor`.
- Keep transport concerns separate from deployment behavior concerns.

## Favor Stable Ownership

- `main.py` should primarily parse CLI input, orchestrate steps, and present output.
- Modules under `deploy/` should own reusable behavior and policy.
- Target inference and target-related presentation should stay centralized.

## Review Questions

- Is the same rule implemented in more than one command?
- Is there a local path and a remote path that should really share one implementation?
- Is a manager doing too little while the CLI does too much?
- Can a normalization step happen once earlier instead of repeatedly at every call site?
- Does a test exist for every flow that now depends on the shared behavior?

## Validation

- Activate the virtual environment first: `source .venv/bin/activate`
- Run targeted tests when changing a narrow area.
- Run `pytest` when the refactor affects shared behavior.