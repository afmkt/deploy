---
name: structural-refactoring-review
description: 'Review this deployment CLI for structural refactoring opportunities. Use when looking for duplicated logic, local-vs-remote branching, config fallback drift, repeated path handling, manager overlap, or consistency issues that should be fixed through shared abstractions instead of patching individual code paths.'
argument-hint: 'Describe the area to review, for example: target handling, proxy/service consistency, config loading, or the whole repo.'
user-invocable: true
disable-model-invocation: false
---

# Structural Refactoring Review

Use this skill when the goal is to improve correctness and maintainability through code structure, not local patches.

This repository already has a few important shared seams:

- `deploy/target.py` for target semantics and local-vs-remote decisions.
- Connection backends in `deploy/local.py` and `deploy/ssh.py`.
- Manager modules such as `deploy/proxy.py`, `deploy/service.py`, `deploy/remote.py`, and `deploy/docker.py`.
- CLI orchestration in `main.py`.

## Review Goals

Prioritize findings that reduce behavior drift across commands and simplify the conceptual model.

- Treat argument resolution and command workflow as orthogonal concerns. Resolution should produce normalized intent; workflow should execute from resolved intent without re-resolving.
- Prefer one shared helper over repeated per-command conditionals.
- Prefer manager-level or helper-level refactors over CLI-only fixes.
- Prefer a single source of truth for defaults, target inference, path layout, and config fallback behavior.
- Prefer changes that make local and remote execution behave the same except for transport.
- Prefer changes that eliminate the need to document special behavior for individual workflows under certain operation targets; when all workflows share the same behavior, the description is simpler and fewer edge cases exist.

## What To Look For

Inspect the code for these patterns:

1. Repeated local-versus-remote branching that should live in one helper or connection abstraction.
2. The same default path, network rule, or config fallback logic repeated across commands.
3. Similar validation or normalization logic implemented in both CLI handlers and manager modules.
4. Workflow code that re-computes defaults, paths, targets, or config fallback instead of consuming a resolved argument object.
5. Features implemented in one command group but not structurally available to related command groups.
6. CLI code in `main.py` that is doing business logic which should live in a module under `deploy/`.
7. Tests that validate behavior in one flow but leave equivalent flows uncovered.
8. Special case handling for individual workflows under specific operation targets (local vs. remote) that could be unified; unified behavior will have a simpler description and fewer edge cases.

## Procedure

1. Identify the user-requested area or scan the whole repo if no area is specified.
2. Trace the current behavior through the CLI entrypoint and the relevant manager/helper modules.
3. Group duplicated behavior by underlying concern, such as target resolution, ingress networking, config loading, or filesystem layout.
4. Propose the smallest structural change that removes duplication at the source.
5. If editing code, implement the shared abstraction first, separating argument resolution from workflow execution, then simplify the call sites.
6. Add or update tests so the shared behavior is covered in all affected flows.
7. In the final response, report findings in priority order and explain why each one is structural rather than cosmetic.

## Orthogonality Contract

When designing or reviewing command flows, enforce this contract:

1. Resolve: Convert CLI/config/session inputs into a resolved argument object with all defaults and fallback decisions finalized.
2. Execute: Run workflow logic strictly from resolved arguments; avoid reading raw CLI/config again in execution steps.
3. Persist on success: Write resolved configuration or session state only after successful execution.

If a command cannot be described with this contract, treat it as a refactoring opportunity.

## Repo-Specific Heuristics

- If the same local/remote rule appears in more than one command, consider `deploy/target.py` or the connection layer first.
- If path or deployment metadata handling repeats, prefer centralization in the responsible manager module.
- If the CLI is converting, normalizing, or reconciling inputs repeatedly, move that logic out of `main.py` unless it is strictly presentation-related.
- If execution logic still depends on unresolved/raw arguments, add an explicit resolver seam and pass a resolved object through the workflow.
- If proxy and service behavior must stay aligned, look for a shared representation rather than mirrored logic.
- When you find a workflow that behaves differently under different operation targets, ask: can this be unified? Unified behavior is easier to describe, test, and maintain. A refactor that eliminates a special case is worth the effort.

## Output Expectations

When used for review, produce:

- A short list of the highest-value refactoring opportunities.
- The root inconsistency each opportunity addresses.
- The shared abstraction or module boundary that should own the fix.
- Any test gaps that should be closed alongside the refactor.

When used for implementation, apply the refactor and validate it with the project test suite after activating the virtual environment.

## References

- [Refactoring checklist](./references/checklist.md)