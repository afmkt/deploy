# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog and follows semantic versioning.

## [Unreleased]

### Added

- Open-source governance docs: LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY
- CI workflow for tests on pull requests and pushes
- Release workflow for tagged binary builds and checksum publishing
- CLI `--version` support

### Changed

- Build script is now uv-first (`uv run pyinstaller --clean deploy.spec`)
- README expanded with uv workflow and binary verification guidance
