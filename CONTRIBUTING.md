# Contributing

Thanks for contributing.

## Development setup

1. Install Python 3.12+
2. Install uv
3. Clone the repository
4. Run tests:

```sh
uv run pytest
```

## Build

Build the standalone binary:

```sh
./scripts/build.sh
```

Expected artifact:

- `dist/deploy`

## Pull request checklist

- Add or update tests for behavior changes
- Ensure `uv run pytest` passes
- Keep changes focused and small
- Update README or docs when command behavior changes
- Add a CHANGELOG entry under `Unreleased`

## Commit and branch guidance

- Use clear commit messages
- Keep feature/fix branches short-lived
- Rebase or merge main before requesting review

## Reporting issues

Open a GitHub issue with:

- command used
- expected behavior
- actual behavior
- relevant logs and environment details
