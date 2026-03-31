# Git Workflow

## Conventional Commits

All commit messages follow the Conventional Commits specification:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Use When |
|------|----------|
| `feat` | Adding a new feature or capability |
| `fix` | Fixing a bug |
| `refactor` | Restructuring code without changing behavior |
| `test` | Adding or updating tests only |
| `docs` | Documentation changes only |
| `chore` | Build, CI, dependency updates, tooling |
| `perf` | Performance improvements |
| `style` | Formatting, whitespace, linting (no logic changes) |
| `ci` | CI/CD pipeline changes |

### Scope

Use the module or component name: `engine`, `agents`, `schemas`, `cli`, `container`, `config`.

### Examples

```
feat(engine): add parallel execution for engineer agents
fix(agents): prevent race condition in artifact handoff
refactor(models): extract speed classification into separate module
test(engine): add integration tests for crash recovery
chore(deps): bump pydantic to 2.6.0
```

## Branch Naming

```
<type>/<short-description>
```

Examples: `feat/parallel-engineers`, `fix/crash-recovery`, `chore/update-deps`.

## Pull Request Workflow

1. Create a feature branch from `master`.
2. Make small, atomic commits. Each commit should compile and pass tests.
3. Before opening a PR, analyze ALL commits on the branch (not just the latest).
4. PR title follows the same conventional commit format.
5. PR body includes a Summary section and a Test Plan section.
6. Request review. Address all feedback before merging.
7. Squash-merge to `master` to keep history clean.

## Commit Hygiene

- Never commit generated files, build artifacts, or IDE configs.
- Never commit `.env` files or any file containing secrets.
- Each commit should be a logical unit of work, not a save point.
- Write commit messages in imperative mood: "add feature" not "added feature."

## Release Workflow

- Tag releases with semantic versioning: `v0.12.0`.
- Update `_version.py` in the same commit as the release.
- Release commits use: `chore(release): bump version to v0.12.0`.
